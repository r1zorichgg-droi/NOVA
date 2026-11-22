"""Применение темы ко всему приложению: палитра Qt + таблица стилей + перерисовка самописных виджетов."""
from __future__ import annotations

from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication

from settings import AppSettings
from ui import theme
from ui.theme import Palette


def _q(color: theme.Color) -> QColor:
    return QColor(color[0], color[1], color[2], color[3])


def build_app_font() -> QFont:
    font = QFont()
    font.setFamilies(["Segoe UI Variable Text", "Segoe UI", "Inter", "Arial"])
    font.setPixelSize(13)
    font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    return font


def apply_theme(app: QApplication, settings: AppSettings) -> Palette:
    pal = theme.build_palette(str(settings.get("theme")), str(settings.get("accent")))
    theme.set_current(pal)

    qp = QPalette()
    R = QPalette.ColorRole
    for role, color in (
            (R.Window, pal.bg), (R.WindowText, pal.text), (R.Base, pal.card_alt), (R.AlternateBase, pal.card),
            (R.Text, pal.text), (R.Button, pal.card_alt), (R.ButtonText, pal.text), (R.ToolTipBase, pal.card_alt),
            (R.ToolTipText, pal.text), (R.Highlight, pal.accent), (R.HighlightedText, pal.on_accent),
            (R.PlaceholderText, pal.dim), (R.Link, pal.accent_fg)):
        qp.setColor(role, _q(color))
    app.setPalette(qp)
    app.setStyleSheet(theme.build_stylesheet(pal))
    for widget in app.allWidgets():   # самописные виджеты рисуют цвета из theme.current() — просим их перерисоваться
        widget.update()
    return pal
