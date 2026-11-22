"""Каталог приложений NOVA: прямые установщики и установка через WinGet без перехода на сайты."""
from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QIODevice, QProcess, QSaveFile, QStandardPaths, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import (
    QButtonGroup, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QProgressDialog, QPushButton, QVBoxLayout,
)

from config import resource_path
from ui.widgets import Card, Page, make_label

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class InstallItem:
    name: str
    category: str
    description: str
    badge: str
    accent: str
    mode: str = "download"  # download, winget_download, winget_install или pwa
    url: str = ""
    filename: str = ""
    kind: str = "exe"       # exe или msi
    package_id: str = ""
    source: str = ""        # например msstore
    note: str = ""
    fallback_url: str = ""  # официальная страница издателя на случай отказа прямой загрузки


# Прямые URL ведут на CDN разработчиков. Для приложений с меняющимися адресами
# используется WinGet: он получает актуальный пакет из официального каталога и запускает установку.
INSTALLERS: tuple[InstallItem, ...] = (
    # Игровые платформы
    InstallItem("Steam", "Игровые платформы", "Магазин игр, библиотека и обновления", "S", "#1b83c9",
                url="https://cdn.akamai.steamstatic.com/client/installer/SteamSetup.exe", filename="SteamSetup.exe"),
    InstallItem("Epic Games Launcher", "Игровые платформы", "Epic Games Store, Fortnite и библиотека игр", "EP", "#454545",
                url="https://launcher-public-service-prod06.ol.epicgames.com/launcher/api/installer/download/EpicGamesLauncherInstaller.msi",
                filename="EpicGamesLauncherInstaller.msi", kind="msi"),
    # Прямая ссылка на официальный CDN Rockstar. Обходим часто меняющийся WinGet-манифест.
    InstallItem("Rockstar Games Launcher", "Игровые платформы", "Лаунчер и библиотека Rockstar Games", "R*", "#e8b32a",
                url="https://gamedownloads.rockstargames.com/public/installer/Rockstar-Games-Launcher.exe",
                filename="Rockstar-Games-Launcher.exe",
                fallback_url="https://socialclub.rockstargames.com/rockstar-games-launcher"),

    # Актуальный официальный установщик Prism Launcher с GitHub релизов проекта.
    InstallItem("Prism Launcher", "Игровые платформы", "Лаунчер для отдельных сборок Minecraft, модов и модпаков", "PR", "#5378b8",
                url="https://github.com/PrismLauncher/PrismLauncher/releases/download/11.1.1/PrismLauncher-Windows-MSVC-Setup-11.1.1.exe",
                filename="PrismLauncher-Setup-11.1.1.exe",
                note="Официальный установщик Prism Launcher. Для авторизованной сетевой игры нужна учётная запись с лицензией Minecraft.",
                fallback_url="https://prismlauncher.org/download/windows/"),

    # Официальная Windows Legacy-версия лаунчера Minecraft от Mojang. Это не сторонний Legacy Launcher.
    InstallItem("Minecraft Launcher — Windows Legacy (Mojang)", "Игровые платформы", "Официальный автономный установщик старой версии Minecraft Launcher", "MC", "#55a64b",
                url="https://launcher.mojang.com/download/MinecraftInstaller.msi?ref=mcnet",
                filename="MinecraftInstaller-WindowsLegacy.msi", kind="msi",
                note="Официальная Windows Legacy-версия с сайта Minecraft. Для игры требуется лицензия Minecraft.",
                fallback_url="https://www.minecraft.net/ru-ru/download"),

    # Музыкальные сервисы
    InstallItem("Spotify", "Музыка", "Музыка, плейлисты и подкасты", "SP", "#1db954",
                url="https://download.scdn.co/SpotifySetup.exe", filename="SpotifySetup.exe"),
    InstallItem("SoundCloud", "Музыка", "Официальный Desktop Player в виде веб-приложения", "SC", "#f47721",
                mode="pwa", url="https://soundcloud.com",
                note="SoundCloud не предлагает отдельный официальный EXE для актуальной Windows. Открой SoundCloud в Chrome, Brave или Edge и нажми значок «Установить приложение» в адресной строке."),

    # Браузеры
    InstallItem("Google Chrome", "Браузеры", "Быстрый браузер Google", "CH", "#4285f4",
                url="https://dl.google.com/chrome/install/latest/chrome_installer.exe", filename="ChromeSetup.exe"),
    InstallItem("Mozilla Firefox", "Браузеры", "Браузер с настройками приватности", "FF", "#e87528",
                url="https://download.mozilla.org/?product=firefox-latest-ssl&os=win64&lang=ru", filename="Firefox-Installer.exe"),
    InstallItem("Opera GX", "Браузеры", "Браузер с игровыми функциями и ограничителями", "GX", "#d94470",
                mode="winget_download", package_id="Opera.OperaGX"),
    InstallItem("Brave", "Браузеры", "Защита от рекламы и трекеров", "B", "#e58c37",
                url="https://laptop-updates.brave.com/latest/winx64", filename="BraveBrowserSetup.exe"),

    # Общение
    InstallItem("Discord", "Общение", "Голосовые каналы, чаты и сообщества", "D", "#6575e8",
                url="https://discord.com/api/downloads/distributions/app/installers/latest?channel=stable&platform=win&arch=x64",
                filename="DiscordSetup.exe"),
    InstallItem("Telegram Desktop", "Общение", "Мессенджер для компьютера", "TG", "#299bd6",
                url="https://telegram.org/dl/desktop/win64", filename="TelegramSetup.exe"),

    # Полезные программы
    InstallItem("WinRAR", "Утилиты", "Архиватор RAR, ZIP и других форматов", "RAR", "#7d8b99",
                mode="winget_download", package_id="RARLab.WinRAR"),
    InstallItem("OBS Studio", "Утилиты", "Запись экрана и трансляции", "OBS", "#8a8a9a",
                mode="winget_download", package_id="OBSProject.OBSStudio"),
    InstallItem("Visual Studio Code", "Утилиты", "Редактор кода и Python-проектов", "VS", "#277db7",
                url="https://update.code.visualstudio.com/latest/win32-x64-user/stable", filename="VSCodeSetup.exe"),
)

CATEGORIES = ("Все", "Игровые платформы", "Музыка", "Браузеры", "Общение", "Утилиты")


class InstallPage(Page):
    """Центр загрузки: прямые установщики запускаются сразу, остальные идут через WinGet."""

    def __init__(self) -> None:
        super().__init__("Центр приложений", "Нужные программы и игровые лаунчеры — скачивание и установка в пару кликов.")
        self._manager = QNetworkAccessManager(self)
        self._reply: QNetworkReply | None = None
        self._save_file: QSaveFile | None = None
        self._progress: QProgressDialog | None = None
        self._current_item: InstallItem | None = None
        self._current_path: Path | None = None
        self._category = "Все"
        self._tile_records: list[tuple[InstallItem, object, object]] = []
        self._sections: dict[str, object] = {}
        self._winget_process: QProcess | None = None
        self._winget_log = ""
        self._installing = False

        hero = Card(spacing=8)
        hero_row = QHBoxLayout()
        hero_icon = QLabel()
        hero_icon.setFixedSize(54, 54)
        hero_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo = QPixmap(str(resource_path("assets", "icons", "nova.png")))
        if not logo.isNull():
            hero_icon.setPixmap(logo.scaled(54, 54, Qt.AspectRatioMode.KeepAspectRatio,
                                            Qt.TransformationMode.SmoothTransformation))
        hero_icon.setStyleSheet("background:transparent;border-radius:13px;")
        hero_row.addWidget(hero_icon)
        hero_text = QVBoxLayout()
        hero_text.setSpacing(3)
        hero_text.addWidget(make_label("NOVA App Hub", "h2"))
        hero_text.addWidget(make_label(f"{len(INSTALLERS)} программ · {len(CATEGORIES) - 1} категорий", "muted"))
        hero_row.addLayout(hero_text, 1)
        folder_button = QPushButton("Папка установщиков")
        folder_button.setObjectName("flat")
        folder_button.setCursor(Qt.CursorShape.PointingHandCursor)
        folder_button.clicked.connect(self._open_installer_folder)
        hero_row.addWidget(folder_button)
        hero.body.addLayout(hero_row)
        hero.body.addWidget(make_label(
            "Прямые установщики и файлы WinGet сохраняются в «Downloads/NOVA Installers» и запускаются автоматически. "
            "Prism Launcher — сторонний проект; для авторизованной сетевой игры используется легальная учётная запись Minecraft.",
            "caption", wrap=True,
        ))
        self.body.addWidget(hero)

        self.search = QLineEdit()
        self.search.setPlaceholderText("⌕  Поиск приложения…")
        self.search.setClearButtonEnabled(True)
        self.search.setMinimumHeight(42)
        self.search.textChanged.connect(self._apply_filters)
        self.body.addWidget(self.search)

        filters_card = Card(margins=(12, 10, 12, 10), spacing=6)
        filter_row = QHBoxLayout()
        filter_row.setSpacing(6)
        self.category_group = QButtonGroup(self)
        self.category_group.setExclusive(True)
        self.category_buttons: dict[str, QPushButton] = {}
        for category in CATEGORIES:
            button = QPushButton(category)
            button.setObjectName("chip")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _checked=False, c=category: self._set_category(c))
            self.category_group.addButton(button)
            self.category_buttons[category] = button
            filter_row.addWidget(button)
        self.category_buttons["Все"].setChecked(True)
        filters_card.body.addLayout(filter_row)
        self.body.addWidget(filters_card)

        self.results_label = make_label("Все приложения", "h2")
        self.body.addWidget(self.results_label)
        self.results_container = QFrame()
        self.results_layout = QVBoxLayout(self.results_container)
        self.results_layout.setContentsMargins(0, 0, 0, 0)
        self.results_layout.setSpacing(14)
        self.body.addWidget(self.results_container)
        self.empty_label = make_label("Ничего не найдено. Попробуй другое название.", "muted")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setMinimumHeight(70)
        self.empty_label.hide()
        self.body.addWidget(self.empty_label)

        self._build_catalog()
        self._apply_filters()

        footer = Card(margins=(16, 12, 16, 12), spacing=5)
        footer.body.addWidget(make_label("Безопасная установка", "h3"))
        footer.body.addWidget(make_label(
            "NOVA не устанавливает программы скрытно и не отключает защиту Windows. "
            "При необходимости Windows покажет стандартное подтверждение UAC. "
            "Файлы сохраняются в «Downloads/NOVA Installers». Для части приложений используется WinGet, "
            "а Microsoft Store устанавливает Store-пакеты напрямую; ни один пункт не открывает сайт в браузере.",
            "caption", wrap=True,
        ))
        self.body.addWidget(footer)
        self.finish()

    @staticmethod
    def _installer_folder() -> Path:
        # Keep downloaded setup files in a visible Downloads folder.
        downloads = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DownloadLocation)
        return Path(downloads or str(Path.home() / "Downloads")) / "NOVA Installers"

    def _open_installer_folder(self) -> None:
        folder = self._installer_folder()
        try:
            folder.mkdir(parents=True, exist_ok=True)
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))
        except OSError as exc:
            QMessageBox.warning(self, "Папка установщиков", str(exc))

    def _build_catalog(self) -> None:
        categories: dict[str, list[InstallItem]] = {}
        for item in INSTALLERS:
            categories.setdefault(item.category, []).append(item)
        for category, items in categories.items():
            section = Card(spacing=12)
            header = QHBoxLayout()
            header.addWidget(make_label(category, "h2"))
            header.addStretch(1)
            header.addWidget(make_label(str(len(items)), "caption"))
            section.body.addLayout(header)
            grid = QGridLayout()
            grid.setHorizontalSpacing(10)
            grid.setVerticalSpacing(10)
            grid.setColumnStretch(0, 1)
            grid.setColumnStretch(1, 1)
            for index, item in enumerate(items):
                tile = self._make_tile(item)
                grid.addWidget(tile, index // 2, index % 2)
                self._tile_records.append((item, tile, section))
            section.body.addLayout(grid)
            self.results_layout.addWidget(section)
            self._sections[category] = section

    def _make_tile(self, item: InstallItem) -> QFrame:
        tile = QFrame()
        tile.setObjectName("cardAlt")
        tile.setMinimumHeight(102)
        tile_layout = QHBoxLayout(tile)
        tile_layout.setContentsMargins(12, 10, 12, 10)
        tile_layout.setSpacing(10)
        badge = QLabel(item.badge)
        badge.setFixedSize(42, 42)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setStyleSheet(f"background:{item.accent};color:white;border-radius:12px;font-size:13px;font-weight:800;")
        tile_layout.addWidget(badge, 0, Qt.AlignmentFlag.AlignVCenter)
        text_column = QVBoxLayout()
        text_column.setSpacing(3)
        title = make_label(item.name, "h3")
        title.setToolTip(item.name)
        description = make_label(item.description, "caption", wrap=True)
        description.setToolTip(item.note or item.description)
        text_column.addWidget(title)
        text_column.addWidget(description)
        if item.mode == "download":
            install_hint, button_text = "Официальный EXE/MSI · сохранит и запустит", "Скачать и запустить"
        elif item.mode == "winget_download":
            install_hint, button_text = "WinGet · скачает в папку и запустит", "Скачать и запустить"
        elif item.mode == "pwa":
            install_hint, button_text = "Официальное веб-приложение · без EXE", "Открыть установку"
        else:
            install_hint, button_text = "Microsoft Store · установка через WinGet", "Скачать и установить"
        text_column.addWidget(make_label(install_hint, "caption"))
        tile_layout.addLayout(text_column, 1)
        button = QPushButton(button_text)
        button.setObjectName("primary")
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setMinimumWidth(136)
        default_tip = ("Скачать официальный установщик и открыть его" if item.mode == "download" else
                       f"Скачать пакет {item.package_id} в папку NOVA Installers и запустить установщик" if item.mode == "winget_download" else
                       item.note if item.mode == "pwa" else
                       f"Установить пакет {item.package_id} через Microsoft Store")
        button.setToolTip(item.note or default_tip)
        button.clicked.connect(lambda _checked=False, entry=item: self._start_install(entry))
        tile_layout.addWidget(button, 0, Qt.AlignmentFlag.AlignVCenter)
        return tile

    def _set_category(self, category: str) -> None:
        self._category = category
        self._apply_filters()

    def _apply_filters(self, *_args) -> None:
        raw_query = self.search.text()
        query = raw_query.strip().casefold() if isinstance(raw_query, str) else ""
        counts: dict[str, int] = {}
        visible_total = 0
        for item, tile, _section in self._tile_records:
            matches_category = self._category == "Все" or item.category == self._category
            searchable = f"{item.name} {item.category} {item.description}".casefold()
            visible = matches_category and (not query or query in searchable)
            tile.setVisible(visible)
            if visible:
                visible_total += 1
                counts[item.category] = counts.get(item.category, 0) + 1
        for category, section in self._sections.items():
            section.setVisible(counts.get(category, 0) > 0)
        self.results_label.setText(f"Найдено приложений: {visible_total}")
        self.empty_label.setVisible(visible_total == 0)

    def _start_install(self, item: InstallItem) -> None:
        if self._reply is not None or self._installing:
            QMessageBox.information(self, "Установка уже идёт", "Дождись завершения текущей загрузки или установки.")
            return
        if item.mode == "download":
            self._download(item)
        elif item.mode == "winget_download":
            self._download_via_winget(item)
        elif item.mode == "pwa":
            self._open_pwa_install(item)
        else:
            self._install_via_winget(item)

    def _open_pwa_install(self, item: InstallItem) -> None:
        """Open an official browser-installable web app when the publisher ships no Windows EXE."""
        # Prefer browsers whose PWA installer is documented by SoundCloud.
        browser_candidates = [
            shutil.which("msedge.exe"), shutil.which("msedge"),
            shutil.which("chrome.exe"), shutil.which("chrome"),
            shutil.which("brave.exe"), shutil.which("brave"),
        ]
        env = os.environ
        roots = [Path(env.get(key, "")) for key in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA") if env.get(key)]
        for root in roots:
            browser_candidates.extend([
                str(root / "Microsoft" / "Edge" / "Application" / "msedge.exe"),
                str(root / "Google" / "Chrome" / "Application" / "chrome.exe"),
                str(root / "BraveSoftware" / "Brave-Browser" / "Application" / "brave.exe"),
            ])
        browser = next((candidate for candidate in browser_candidates if candidate and Path(candidate).is_file()), None)
        opened = QProcess.startDetached(browser, [item.url])[0] if browser else False
        if not opened:
            opened = QDesktopServices.openUrl(QUrl(item.url))
        if not opened:
            QMessageBox.warning(self, "Не удалось открыть", f"Не удалось открыть {item.name} в браузере.")
            return
        QMessageBox.information(
            self, f"Установка {item.name}",
            "У SoundCloud нет отдельного официального установщика EXE для Windows 11.\n\n"
            "Открой SoundCloud в Chrome, Brave или Edge, затем нажми значок установки приложения "
            "справа в адресной строке браузера. После этого SoundCloud появится в меню «Пуск» "
            "как отдельное приложение.",
        )

    @staticmethod
    def _find_winget() -> str | None:
        path = shutil.which("winget.exe") or shutil.which("winget")
        if path:
            return path
        local = os.environ.get("LOCALAPPDATA", "")
        candidate = Path(local) / "Microsoft" / "WindowsApps" / "winget.exe" if local else None
        return str(candidate) if candidate and candidate.is_file() else None

    def _download_via_winget(self, item: InstallItem) -> None:
        """Download a package with WinGet, keep its files in NOVA Installers, and run its setup."""
        program = self._find_winget()
        if not program:
            QMessageBox.warning(
                self, "WinGet не найден",
                "Для этой программы нужен Windows Package Manager (WinGet). "
                "Обнови приложение «Установщик приложений» (App Installer) через Microsoft Store и повтори.",
            )
            return
        folder = self._installer_folder()
        safe_id = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in item.package_id)
        stage = folder / safe_id
        try:
            folder.mkdir(parents=True, exist_ok=True)
            if stage.exists():
                shutil.rmtree(stage)
            stage.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            QMessageBox.critical(self, "Папка установщиков", f"Не удалось подготовить папку загрузки:\n{exc}")
            return

        process = QProcess(self)
        process.setProgram(program)
        args = [
            "download", "--id", item.package_id, "--exact",
            "--download-directory", str(stage), "--architecture", "x64",
            "--accept-source-agreements", "--accept-package-agreements",
        ]
        if item.source:
            args.extend(["--source", item.source])
        process.setArguments(args)
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        process.readyReadStandardOutput.connect(lambda: self._read_winget_output(process))
        process.finished.connect(lambda code, status: self._winget_download_finished(item, stage, process, code, status))
        process.errorOccurred.connect(lambda _error: self._winget_error(item, process))
        self._winget_process = process
        self._winget_log = ""
        self._installing = True
        progress = QProgressDialog(f"Скачиваем установщик {item.name}…", "Скрыть", 0, 0, self)
        progress.setWindowTitle("NOVA · загрузка установщика")
        progress.setWindowModality(Qt.WindowModality.NonModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        self._progress = progress
        process.start()

    @staticmethod
    def _installer_file(path: Path) -> bool:
        try:
            if path.stat().st_size < 64_000:
                return False
            with path.open("rb") as stream:
                header = stream.read(8)
            return header.startswith(b"MZ") or header.startswith(bytes.fromhex("D0CF11E0A1B11AE1"))
        except OSError:
            return False

    def _winget_download_finished(self, item: InstallItem, stage: Path, process: QProcess,
                                  code: int, _status) -> None:
        self._read_winget_output(process)
        self._installing = False
        self._winget_process = None
        if self._progress is not None:
            self._progress.close()
            self._progress.deleteLater()
            self._progress = None
        process.deleteLater()
        if int(code) != 0:
            details = self._winget_log.strip()[-3000:]
            QMessageBox.warning(self, "Не удалось скачать", f"WinGet не смог скачать {item.name}.\n\n{details}")
            return

        candidates = [p for p in stage.rglob("*") if p.is_file() and p.suffix.lower() in {".exe", ".msi"}
                      and self._installer_file(p)]
        if not candidates:
            # Some packages are Store/MSIX or portable-only; let WinGet perform its supported install flow.
            QMessageBox.information(
                self, "Особый тип установщика",
                f"Пакет {item.name} не содержит отдельного EXE/MSI. NOVA запустит штатную установку WinGet.",
            )
            self._install_via_winget(item)
            return
        # Dependencies stay next to the main installer inside the package directory.
        candidates.sort(key=lambda file: file.stat().st_size, reverse=True)
        installer = candidates[0]
        if installer.suffix.lower() == ".msi":
            program, args = "msiexec.exe", ["/i", str(installer)]
        else:
            program, args = str(installer), []
        launcher = QProcess(self)
        launcher.setProgram(program)
        launcher.setArguments(args)
        if not launcher.startDetached():
            QMessageBox.warning(self, "Установщик скачан",
                                f"Файл сохранён, но Windows не смогла запустить его автоматически:\n{installer}")
            return
        QMessageBox.information(
            self, "Установщик запущен",
            f"{item.name}: файл скачан через WinGet и запущен.\n\nСохранено в папке:\n{stage}",
        )

    def _install_via_winget(self, item: InstallItem) -> None:
        program = self._find_winget()
        if not program:
            QMessageBox.warning(
                self, "WinGet не найден",
                "Для этой программы нужен Windows Package Manager (WinGet). "
                "Открой Microsoft Store и обнови приложение «Установщик приложений» (App Installer), "
                "затем повтори установку.",
            )
            return
        process = QProcess(self)
        process.setProgram(program)
        args = ["install", "--id", item.package_id, "--exact", "--interactive",
                "--accept-source-agreements", "--accept-package-agreements"]
        if item.source:
            args.extend(["--source", item.source])
        process.setArguments(args)
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        process.readyReadStandardOutput.connect(lambda: self._read_winget_output(process))
        process.finished.connect(lambda code, status: self._winget_finished(item, process, code, status))
        process.errorOccurred.connect(lambda _error: self._winget_error(item, process))
        self._winget_process = process
        self._winget_log = ""
        self._installing = True
        progress = QProgressDialog(f"Подготовка установки {item.name}…", "Скрыть", 0, 0, self)
        progress.setWindowTitle("NOVA · установка приложения")
        progress.setWindowModality(Qt.WindowModality.NonModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        self._progress = progress
        process.start()
        if self._progress is not None:
            self._progress.setLabelText(f"WinGet загружает и запускает установщик {item.name}…")

    def _read_winget_output(self, process: QProcess) -> None:
        try:
            chunk = bytes(process.readAllStandardOutput()).decode("utf-8", errors="replace")
        except Exception:
            chunk = ""
        if chunk:
            self._winget_log = (self._winget_log + chunk)[-12000:]
            if self._progress is not None:
                lines = [line.strip() for line in chunk.splitlines() if line.strip()]
                if lines:
                    self._progress.setLabelText(lines[-1][:120])

    def _winget_finished(self, item: InstallItem, process: QProcess, code: int, _status) -> None:
        self._read_winget_output(process)
        self._installing = False
        self._winget_process = None
        if self._progress is not None:
            self._progress.close()
            self._progress.deleteLater()
            self._progress = None
        process.deleteLater()
        if int(code) == 0:
            QMessageBox.information(self, "Готово", f"{item.name} успешно установлено.")
        else:
            details = self._winget_log.strip()[-3500:]
            QMessageBox.warning(
                self, "Не удалось установить",
                f"WinGet завершил установку {item.name} с кодом {code}.\n\n"
                f"Проверь интернет и наличие WinGet.\n\n{details}",
            )

    def _winget_error(self, item: InstallItem, process: QProcess) -> None:
        if self._winget_process is not process:
            return
        self._installing = False
        self._winget_process = None
        if self._progress is not None:
            self._progress.close()
            self._progress.deleteLater()
            self._progress = None
        QMessageBox.warning(self, "Не удалось запустить WinGet", f"Не получилось запустить установку {item.name}.")
        process.deleteLater()

    def _download(self, item: InstallItem) -> None:
        try:
            folder = self._installer_folder()
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            QMessageBox.critical(self, "Ошибка папки", f"Не удалось создать папку NOVA Installers:\n{exc}")
            return
        destination = folder / item.filename
        save_file = QSaveFile(str(destination))
        if not save_file.open(QIODevice.OpenModeFlag.WriteOnly):
            QMessageBox.critical(self, "Ошибка файла", f"Не удалось сохранить установщик:\n{save_file.errorString()}")
            return
        request = QNetworkRequest(QUrl(item.url))
        request.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute,
                             QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy)
        # Некоторые CDN отклоняют клиентские запросы без обычного браузерного User-Agent.
        request.setRawHeader(b"User-Agent", b"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36 NOVA/1.05.5")
        request.setRawHeader(b"Accept", b"application/octet-stream,application/x-msdownload,*/*;q=0.8")
        if item.name == "Rockstar Games Launcher":
            request.setRawHeader(b"Referer", b"https://socialclub.rockstargames.com/")
        request.setTransferTimeout(90_000)
        reply = self._manager.get(request)
        self._reply = reply
        self._save_file = save_file
        self._current_item = item
        self._current_path = destination
        progress = QProgressDialog(f"Подготовка загрузки {item.name}…", "Отменить", 0, 100, self)
        progress.setWindowTitle("NOVA · загрузка установщика")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.canceled.connect(reply.abort)
        self._progress = progress
        reply.readyRead.connect(self._write_download_chunk)
        reply.downloadProgress.connect(self._update_progress)
        reply.finished.connect(lambda: self._download_finished(reply))

    def _write_download_chunk(self) -> None:
        if self._reply is None or self._save_file is None:
            return
        data = bytes(self._reply.readAll())
        if data and self._save_file.write(data) != len(data):
            self._reply.abort()

    def _update_progress(self, received: int, total: int) -> None:
        if self._progress is None:
            return
        if total > 0:
            self._progress.setRange(0, 100)
            self._progress.setValue(min(100, int(received * 100 / total)))
            name = self._current_item.name if self._current_item else "установщика"
            self._progress.setLabelText(f"Загрузка {name}: {received / 1048576:.1f} / {total / 1048576:.1f} МБ")
        else:
            self._progress.setRange(0, 0)

    def _download_finished(self, reply: QNetworkReply) -> None:
        self._write_download_chunk()
        item, destination, save_file = self._current_item, self._current_path, self._save_file
        error, error_text = reply.error(), reply.errorString()
        status_code = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        reply.deleteLater()
        self._reply = None
        self._save_file = None
        self._current_item = None
        self._current_path = None
        if self._progress is not None:
            self._progress.close()
            self._progress.deleteLater()
            self._progress = None
        if save_file is None or destination is None or item is None:
            return
        if error != QNetworkReply.NetworkError.NoError or (status_code is not None and int(status_code) >= 400):
            save_file.cancelWriting()
            self._offer_official_fallback(item, f"{error_text or f'HTTP {status_code}'}")
            return
        if not save_file.commit():
            QMessageBox.critical(self, "Ошибка сохранения", save_file.errorString())
            return
        try:
            with destination.open("rb") as downloaded:
                header = downloaded.read(8)
                size = destination.stat().st_size
        except OSError as exc:
            QMessageBox.critical(self, "Ошибка файла", str(exc))
            return
        valid = size > 64_000 and (header.startswith(b"MZ") if item.kind == "exe"
                                  else header.startswith(bytes.fromhex("D0CF11E0A1B11AE1")))
        if not valid:
            destination.unlink(missing_ok=True)
            self._offer_official_fallback(item, "Сервер вернул не установщик Windows. Некорректный файл удалён.")
            return
        if item.kind == "msi":
            program, args = "msiexec.exe", ["/i", str(destination)]
        else:
            program, args = str(destination), []
        process = QProcess(self)
        process.setProgram(program)
        process.setArguments(args)
        if not process.startDetached():
            QMessageBox.warning(self, "Установщик скачан",
                                f"Файл сохранён, но Windows не смогла запустить его автоматически:\n{destination}")
            return
        QMessageBox.information(self, "Установщик запущен",
                                f"{item.name}: установщик скачан и запущен.\n\nФайл сохранён:\n{destination}")

    def _offer_official_fallback(self, item: InstallItem, reason: str) -> None:
        """If a publisher CDN blocks a direct request, open only that publisher's official page."""
        if item.fallback_url:
            QMessageBox.warning(
                self, "Прямая загрузка не удалась",
                f"Не удалось скачать {item.name} напрямую.\n\nПричина: {reason}\n\n"
                "Сейчас откроется официальная страница издателя — скачивай установщик только оттуда.",
            )
            QDesktopServices.openUrl(QUrl(item.fallback_url))
        else:
            QMessageBox.critical(self, "Не удалось скачать", f"{item.name}: {reason}")

    def closeEvent(self, event) -> None:  # noqa: N802, ANN001
        if self._reply is not None:
            self._reply.abort()
        if self._winget_process is not None and self._winget_process.state() != QProcess.ProcessState.NotRunning:
            self._winget_process.terminate()
        super().closeEvent(event)
