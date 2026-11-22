"""Палитры и таблица стилей (QSS). Модуль не зависит от Qt — его можно тестировать без PySide6.

Цвет — это кортеж (r, g, b, a). Полупрозрачные цвета (границы, подсветка при наведении) есть
в палитре как обычные значения, а в QSS превращаются в rgba(...).
"""
from __future__ import annotations

from dataclasses import dataclass

Color = tuple[int, int, int, int]

DEFAULT_ACCENT = (124, 92, 255, 255)
WHITE: Color = (255, 255, 255, 255)
BLACK: Color = (0, 0, 0, 255)


def parse_hex(value: str, fallback: Color = DEFAULT_ACCENT) -> Color:
    s = (value or "").strip().lstrip("#")
    if len(s) == 3:
        s = "".join(ch * 2 for ch in s)
    if len(s) != 6:
        return fallback
    try:
        return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16), 255)
    except ValueError:
        return fallback


def to_hex(c: Color) -> str:
    return f"#{c[0]:02x}{c[1]:02x}{c[2]:02x}"


def with_alpha(c: Color, alpha: int) -> Color:
    return (c[0], c[1], c[2], max(0, min(255, int(alpha))))


def mix(a: Color, b: Color, t: float) -> Color:
    """Смешивает два цвета: t=0 -> a, t=1 -> b (итог непрозрачный)."""
    t = max(0.0, min(1.0, t))
    return (round(a[0] + (b[0] - a[0]) * t), round(a[1] + (b[1] - a[1]) * t),
            round(a[2] + (b[2] - a[2]) * t), 255)


def luminance(c: Color) -> float:
    def lin(v: int) -> float:
        x = v / 255.0
        return x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4
    return 0.2126 * lin(c[0]) + 0.7152 * lin(c[1]) + 0.0722 * lin(c[2])


def readable_on(background: Color) -> Color:
    """Чёрный или белый — тот, что контрастнее на данном фоне."""
    lum = luminance(background)
    white_contrast = 1.05 / (lum + 0.05)
    black_contrast = (lum + 0.05) / 0.05
    return WHITE if white_contrast >= black_contrast else BLACK


def css(c: Color) -> str:
    if c[3] >= 255:
        return to_hex(c)
    return f"rgba({c[0]}, {c[1]}, {c[2]}, {c[3]})"


@dataclass(frozen=True)
class Palette:
    name: str
    dark: bool
    bg: Color
    sidebar: Color
    card: Color
    card_alt: Color
    card_hover: Color
    border: Color
    hover: Color
    track: Color
    text: Color
    muted: Color
    dim: Color
    accent: Color
    accent_hover: Color
    accent_soft: Color
    accent_fg: Color      # цвет текста/значков на accent_soft
    on_accent: Color      # цвет текста на сплошном accent
    ok: Color
    warn: Color
    danger: Color
    c_cpu: Color
    c_gpu: Color
    c_ram: Color
    c_down: Color
    c_up: Color


def build_palette(theme: str, accent_hex: str) -> Palette:
    accent = parse_hex(accent_hex)
    dark = theme != "light"
    if dark:
        bg, sidebar = (11, 11, 15, 255), (16, 16, 21, 255)
        card, card_alt, card_hover = (22, 22, 29, 255), (30, 30, 39, 255), (38, 38, 49, 255)
        text, muted, dim = (242, 242, 246, 255), (142, 142, 156, 255), (92, 92, 106, 255)
        border, hover, track = (255, 255, 255, 20), (255, 255, 255, 14), (255, 255, 255, 26)
        accent_fg = mix(accent, WHITE, 0.38)
    else:
        bg, sidebar = (242, 242, 247, 255), (233, 233, 240, 255)
        card, card_alt, card_hover = (255, 255, 255, 255), (246, 246, 250, 255), (238, 238, 245, 255)
        text, muted, dim = (21, 21, 27, 255), (106, 106, 120, 255), (160, 160, 172, 255)
        border, hover, track = (0, 0, 0, 22), (0, 0, 0, 12), (0, 0, 0, 28)
        accent_fg = mix(accent, BLACK, 0.30)
    return Palette(
        name="dark" if dark else "light", dark=dark,
        bg=bg, sidebar=sidebar, card=card, card_alt=card_alt, card_hover=card_hover,
        border=border, hover=hover, track=track, text=text, muted=muted, dim=dim,
        accent=accent, accent_hover=mix(accent, WHITE, 0.15), accent_soft=with_alpha(accent, 46),
        accent_fg=accent_fg, on_accent=readable_on(accent),
        ok=(52, 211, 153, 255), warn=(251, 191, 36, 255), danger=(248, 113, 113, 255),
        c_cpu=accent, c_gpu=(56, 189, 248, 255), c_ram=(52, 211, 153, 255),
        c_down=(56, 189, 248, 255), c_up=(251, 191, 36, 255),
    )


_current: Palette = build_palette("dark", to_hex(DEFAULT_ACCENT))


def current() -> Palette:
    return _current


def set_current(palette: Palette) -> None:
    global _current
    _current = palette


def build_stylesheet(p: Palette) -> str:
    c = {name: css(getattr(p, name)) for name in (
        "bg", "sidebar", "card", "card_alt", "card_hover", "border", "hover", "track", "text", "muted",
        "dim", "accent", "accent_hover", "accent_soft", "accent_fg", "on_accent", "ok", "warn", "danger")}
    return f"""
QWidget#root {{ background: {c['bg']}; }}
QMainWindow {{ background: {c['bg']}; }}
QWidget {{ color: {c['text']}; font-size: 13px; }}
QLabel {{ background: transparent; }}
QLabel#h1 {{ font-size: 28px; font-weight: 600; }}
QLabel#h2 {{ font-size: 18px; font-weight: 600; }}
QLabel#h3 {{ font-size: 14px; font-weight: 600; }}
QLabel#big {{ font-size: 34px; font-weight: 600; }}
QLabel#value {{ font-size: 16px; font-weight: 600; }}
QLabel#muted {{ color: {c['muted']}; }}
QLabel#caption {{ color: {c['muted']}; font-size: 12px; }}
QLabel#ok {{ color: {c['ok']}; }}
QLabel#warn {{ color: {c['warn']}; }}
QLabel#danger {{ color: {c['danger']}; }}

QScrollArea {{ border: none; background: transparent; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 12px; margin: 4px 2px 4px 2px; }}
QScrollBar::handle:vertical {{ background: {c['track']}; border-radius: 4px; min-height: 36px; }}
QScrollBar::handle:vertical:hover {{ background: {c['dim']}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
QScrollBar:horizontal {{ height: 0; }}

QFrame#card {{ background: {c['card']}; border: 1px solid {c['border']}; border-radius: 16px; }}
QFrame#cardAlt {{ background: {c['card_alt']}; border: 1px solid {c['border']}; border-radius: 12px; }}
QFrame#sidebar {{ background: {c['sidebar']}; border: none; border-right: 1px solid {c['border']}; }}
QFrame#line {{ background: {c['border']}; border: none; max-height: 1px; min-height: 1px; }}

QPushButton {{ background: {c['card_alt']}; border: 1px solid {c['border']}; border-radius: 10px;
               padding: 8px 16px; font-weight: 500; }}
QPushButton:hover {{ background: {c['card_hover']}; }}
QPushButton:pressed {{ background: {c['track']}; }}
QPushButton:disabled {{ color: {c['dim']}; background: transparent; }}
QPushButton#primary {{ background: {c['accent']}; color: {c['on_accent']}; border: none; }}
QPushButton#primary:hover {{ background: {c['accent_hover']}; }}
QPushButton#primary:disabled {{ background: {c['track']}; color: {c['dim']}; }}
QPushButton#danger {{ color: {c['danger']}; }}
QPushButton#destructive {{ background: {c['danger']}; color: #1c0a0a; border: none; font-weight: 600; }}
QPushButton#destructive:hover {{ background: #fca5a5; }}
QPushButton#destructive:disabled {{ background: {c['track']}; color: {c['dim']}; }}

QFrame#segwrap {{ background: {c['card_alt']}; border: 1px solid {c['border']}; border-radius: 11px; }}
QPushButton#seg {{ background: transparent; border: none; border-radius: 8px; padding: 6px 14px; color: {c['muted']}; }}
QPushButton#seg:hover {{ color: {c['text']}; }}
QPushButton#seg:checked {{ background: {c['accent_soft']}; color: {c['accent_fg']}; font-weight: 600; }}
QPushButton#chip {{ background: {c['card_alt']}; border: 1px solid {c['border']}; border-radius: 15px;
                    padding: 6px 14px; color: {c['muted']}; }}
QPushButton#chip:hover {{ color: {c['text']}; }}
QPushButton#chip:checked {{ background: {c['accent_soft']}; border-color: {c['accent']}; color: {c['accent_fg']}; }}

QLineEdit {{ background: {c['card_alt']}; border: 1px solid {c['border']}; border-radius: 10px;
             padding: 8px 12px; selection-background-color: {c['accent']}; selection-color: {c['on_accent']}; }}
QLineEdit:focus {{ border-color: {c['accent']}; }}

QSlider::groove:horizontal {{ height: 4px; background: {c['track']}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {c['accent']}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: #ffffff; border: 1px solid {c['border']}; width: 16px; height: 16px;
                              margin: -7px 0; border-radius: 8px; }}
QSlider:disabled::sub-page:horizontal {{ background: {c['dim']}; }}

QToolTip {{ background: {c['card_alt']}; color: {c['text']}; border: 1px solid {c['border']}; padding: 6px 8px; }}
QMenu {{ background: {c['card']}; border: 1px solid {c['border']}; border-radius: 10px; padding: 6px; }}
QMenu::item {{ padding: 8px 22px; border-radius: 6px; }}
QMenu::item:selected {{ background: {c['accent_soft']}; color: {c['accent_fg']}; }}
QMenu::separator {{ height: 1px; background: {c['border']}; margin: 6px 8px; }}
QMessageBox {{ background: {c['bg']}; }}
QDialog {{ background: {c['bg']}; }}

QPlainTextEdit {{ background: {c['card_alt']}; border: 1px solid {c['border']}; border-radius: 10px;
                  padding: 6px 10px; selection-background-color: {c['accent']}; selection-color: {c['on_accent']}; }}
QPlainTextEdit:focus {{ border-color: {c['accent']}; }}
QSpinBox {{ background: {c['card_alt']}; border: 1px solid {c['border']}; border-radius: 10px; padding: 6px 10px; }}
QSpinBox:focus {{ border-color: {c['accent']}; }}
QComboBox {{ background: {c['card_alt']}; border: 1px solid {c['border']}; border-radius: 10px; padding: 7px 12px; }}
QComboBox:hover {{ background: {c['card_hover']}; }}
QComboBox:focus {{ border-color: {c['accent']}; }}
QComboBox::drop-down {{ border: none; width: 26px; }}
QComboBox QAbstractItemView {{ background: {c['card']}; border: 1px solid {c['border']}; outline: none; padding: 4px;
                               selection-background-color: {c['accent_soft']}; selection-color: {c['accent_fg']}; }}
QCheckBox {{ spacing: 10px; background: transparent; }}
QCheckBox::indicator {{ width: 18px; height: 18px; border-radius: 5px; border: 1px solid {c['dim']}; background: {c['card_alt']}; }}
QCheckBox::indicator:hover {{ border-color: {c['accent']}; }}
QCheckBox::indicator:checked {{ background: {c['accent']}; border-color: {c['accent']}; }}
QListWidget {{ background: {c['card_alt']}; border: 1px solid {c['border']}; border-radius: 10px; padding: 4px; outline: none; }}
QListWidget::item {{ padding: 7px 8px; border-radius: 6px; }}
QListWidget::item:hover {{ background: {c['hover']}; }}
QListWidget::item:selected {{ background: {c['accent_soft']}; color: {c['accent_fg']}; }}
QTreeWidget {{ background: {c['card_alt']}; border: 1px solid {c['border']}; border-radius: 10px; padding: 4px; outline: none; }}
QTreeWidget::item {{ padding: 5px 4px; border-radius: 6px; }}
QTreeWidget::item:hover {{ background: {c['hover']}; }}
QTreeWidget::item:selected {{ background: {c['accent_soft']}; color: {c['accent_fg']}; }}
QTreeWidget::indicator {{ width: 18px; height: 18px; border-radius: 5px; border: 1px solid {c['dim']}; background: {c['card']}; }}
QTreeWidget::indicator:hover {{ border-color: {c['accent']}; }}
QTreeWidget::indicator:checked {{ background: {c['accent']}; border-color: {c['accent']}; }}
QTreeWidget::indicator:disabled {{ border-color: {c['border']}; background: transparent; }}
QHeaderView::section {{ background: transparent; color: {c['muted']}; border: none; border-bottom: 1px solid {c['border']};
                        padding: 6px 8px; font-weight: 600; }}
QProgressBar {{ background: {c['track']}; border: none; border-radius: 5px; max-height: 10px; min-height: 10px;
                text-align: center; color: transparent; }}
QProgressBar::chunk {{ background: {c['accent']}; border-radius: 5px; }}
QPushButton#flat {{ background: transparent; border: none; color: {c['muted']}; padding: 6px 10px; }}
QPushButton#flat:hover {{ color: {c['text']}; background: {c['hover']}; }}
QPushButton#dots {{ padding: 0; font-size: 18px; font-weight: 700; border-radius: 10px; }}
QPushButton#big {{ background: {c['accent']}; color: {c['on_accent']}; border: none; border-radius: 14px;
                   font-size: 16px; font-weight: 700; padding: 14px 34px; }}
QPushButton#big:hover {{ background: {c['accent_hover']}; }}
QPushButton#big:disabled {{ background: {c['track']}; color: {c['dim']}; }}
QFrame#toast {{ background: {c['card']}; border: 1px solid {c['border']}; border-radius: 12px; }}
"""
