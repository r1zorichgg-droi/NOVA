"""Окно NOVA Dynamic Island: безрамочная прозрачная капсула сверху по центру экрана.

Виды (view):
  compact_metrics — компактные показатели ПК            exp_music — раскрытый плеер
  compact_music   — мини-плеер (когда играет музыка)    exp_stats — раскрытая статистика
  toast           — короткое уведомление (USB, громкость, запуск приложения, ...)
  exp_apps        — вкладка «Приложения»: плитки закреплённых программ, клик запускает

Окно всегда ровно по размеру капсулы (клики мимо капсулы проходят к окнам под ней),
а размер анимируется покадровым setGeometry. Содержимое рисуется вручную через QPainter.
"""
from __future__ import annotations

import logging
import time
from dataclasses import replace
from typing import TYPE_CHECKING

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QGuiApplication, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QWidget

from config import ISLAND_METRICS
from island import launcher_tab as lt
from island.island_animations import BASE_DURATION_MS, Fade, SizeTween
from island.island_monitor import Chip, build_chips, build_tiles
from island.model import MusicState, Notification, animation_scale, bar_heights
from island.music_controller import MusicController
from settings import AppSettings
from system import winapi
from system.collector import Snapshot
from system.fmt import NA, fmt_clock, fmt_uptime
from system.sampler import Sampler

if TYPE_CHECKING:
    from launcher.library_manager import LibraryManager
    from ui.icon_cache import IconCache

log = logging.getLogger(__name__)

EXP_W, EXP_MUSIC_H, EXP_STATS_H = 480.0, 188.0, 208.0
MAX_RADIUS = 34.0
CHIP_GAP, COMPACT_PAD = 14.0, 18.0
SAMPLED_METRICS = {"cpu", "gpu", "ram", "cpu_temp", "gpu_temp", "net"}
LAYOUT_KEYS = {"island_width", "island_height", "island_scale", "island_offset_x", "island_offset_y",
               "island_metrics", "island_music_mode", "animations", "island_launcher_enabled"}
DROP_SUFFIXES = (".exe", ".lnk", ".bat", ".cmd")

BG = QColor(12, 12, 16)
EDGE = QColor(255, 255, 255, 26)
TEXT = QColor(245, 245, 248)
MUTED = QColor(138, 138, 151)
ALIGN_L = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
ALIGN_R = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
ALIGN_C = Qt.AlignmentFlag.AlignCenter


def _font(px: int, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    font = QFont()
    font.setFamilies(["Segoe UI Variable Text", "Segoe UI", "Inter", "Arial"])
    font.setPixelSize(px)
    font.setWeight(weight)
    return font


def _alpha(color: QColor, a: float) -> QColor:
    c = QColor(color)
    c.setAlphaF(min(max(a, 0.0), 1.0))
    return c


def _poly(points: list[tuple[float, float]]) -> QPainterPath:
    path = QPainterPath()
    path.moveTo(QPointF(*points[0]))
    for pt in points[1:]:
        path.lineTo(QPointF(*pt))
    path.closeSubpath()
    return path


def _text(p: QPainter, rect: QRectF, text: str, font: QFont, color: QColor,
          align: Qt.AlignmentFlag = ALIGN_L, elide: bool = True) -> None:
    if elide:
        text = QFontMetrics(font).elidedText(text, Qt.TextElideMode.ElideRight, max(int(rect.width()), 1))
    p.setFont(font)
    p.setPen(color)
    p.drawText(rect, align, text)


def _draw_transport(p: QPainter, kind: str, cx: float, cy: float, s: float, color: QColor, enabled: bool = True) -> None:
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(_alpha(color, 1.0 if enabled else 0.28))
    bar = 0.15 * s
    if kind == "play":
        p.drawPath(_poly([(cx - 0.32 * s, cy - 0.5 * s), (cx + 0.52 * s, cy), (cx - 0.32 * s, cy + 0.5 * s)]))
    elif kind == "pause":
        w = 0.27 * s
        p.drawRoundedRect(QRectF(cx - 0.45 * s, cy - 0.5 * s, w, s), w / 3, w / 3)
        p.drawRoundedRect(QRectF(cx + 0.45 * s - w, cy - 0.5 * s, w, s), w / 3, w / 3)
    elif kind == "next":
        p.drawPath(_poly([(cx - 0.5 * s, cy - 0.5 * s), (cx + 0.3 * s, cy), (cx - 0.5 * s, cy + 0.5 * s)]))
        p.drawRoundedRect(QRectF(cx + 0.34 * s, cy - 0.5 * s, bar, s), bar / 3, bar / 3)
    elif kind == "prev":
        p.drawPath(_poly([(cx + 0.5 * s, cy - 0.5 * s), (cx - 0.3 * s, cy), (cx + 0.5 * s, cy + 0.5 * s)]))
        p.drawRoundedRect(QRectF(cx - 0.34 * s - bar, cy - 0.5 * s, bar, s), bar / 3, bar / 3)


def _draw_kind_icon(p: QPainter, kind: str, cx: float, cy: float, d: float, color: QColor, muted: bool = False) -> None:
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(_alpha(color, 0.18))
    p.drawEllipse(QPointF(cx, cy), d / 2, d / 2)
    k = d / 28.0
    pen = QPen(color, 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    if kind == "volume":
        p.drawPath(_poly([(cx - 6 * k, cy - 3 * k), (cx - 2 * k, cy - 3 * k), (cx + 2.5 * k, cy - 6.5 * k),
                          (cx + 2.5 * k, cy + 6.5 * k), (cx - 2 * k, cy + 3 * k), (cx - 6 * k, cy + 3 * k)]))
        if muted:
            p.drawLine(QPointF(cx + 5 * k, cy - 3 * k), QPointF(cx + 10 * k, cy + 3 * k))
            p.drawLine(QPointF(cx + 10 * k, cy - 3 * k), QPointF(cx + 5 * k, cy + 3 * k))
        else:
            p.drawArc(QRectF(cx - 1 * k, cy - 5 * k, 10 * k, 10 * k), -50 * 16, 100 * 16)
    elif kind == "app":
        p.drawRoundedRect(QRectF(cx - 7 * k, cy - 5.5 * k, 14 * k, 11 * k), 2.5 * k, 2.5 * k)
        p.drawLine(QPointF(cx - 7 * k, cy - 2 * k), QPointF(cx + 7 * k, cy - 2 * k))
    elif kind == "boost":      # молния — режим производительности
        p.drawPath(_poly([(cx + 2 * k, cy - 8 * k), (cx - 5 * k, cy + 1 * k), (cx - 0.5 * k, cy + 1 * k),
                          (cx - 2 * k, cy + 8 * k), (cx + 5 * k, cy - 2 * k), (cx + 0.5 * k, cy - 2 * k)]))
    else:
        glyph = {"usb": "USB", "music": "♪"}.get(kind, "i")
        p.setFont(_font(int(d * (0.3 if kind == "usb" else 0.52)), QFont.Weight.DemiBold))
        p.drawText(QRectF(cx - d / 2, cy - d / 2, d, d), ALIGN_C, glyph)


def _temp_color(fraction: float) -> QColor:
    if fraction < 0.6:
        return QColor(52, 211, 153)
    if fraction < 0.8:
        return QColor(251, 191, 36)
    return QColor(248, 113, 113)


class IslandWindow(QWidget):
    settings_requested = Signal()      # клик по плитке «+»: открыть настройки острова в главном окне

    def __init__(self, settings: AppSettings, sampler: Sampler, music: MusicController, parent=None) -> None:
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                            | Qt.WindowType.Tool | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setMouseTracking(True)
        self.setAcceptDrops(True)       # EXE и ярлыки можно перетащить на остров, чтобы добавить в «Приложения»
        self.setWindowTitle("NOVA Island")

        self._settings, self._sampler, self._music_ctl = settings, sampler, music
        self._snap: Snapshot | None = None
        self._music: MusicState | None = None
        self._music_recv = time.monotonic()
        self._paused_since: float | None = None
        self._pix: QPixmap | None = None
        self._cover_cache: tuple[object, QPixmap] | None = None
        self._mode, self._page, self._view = "compact", "music", "compact_metrics"
        self._toast: Notification | None = None
        self._size = (float(settings.get("island_width")), float(settings.get("island_height")))
        self._target = self._size
        self._alpha = 1.0
        self._bars_level = 0.0
        self._fullscreen_active = False
        self._game_hidden = False
        self._hit: dict[str, QRectF] = {}
        self._dots: list[tuple[str, QRectF]] = []
        self._xform = (0.0, 0.0, 1.0)
        self._library: LibraryManager | None = None
        self._icons: IconCache | None = None
        self._tiles: list[tuple[str, int, str, QPixmap]] = []      # (вид, id, название, значок)
        self._hover_slot: int | None = None
        self._footer_msg: tuple[str, bool] | None = None           # (текст, это ошибка)
        self._drag_expanded = False

        self._f_label, self._f_value = _font(11, QFont.Weight.Medium), _font(12, QFont.Weight.DemiBold)
        self._f_title, self._f_artist = _font(16, QFont.Weight.DemiBold), _font(14)
        self._f_small = _font(12, QFont.Weight.Medium)
        self._fm_label, self._fm_value = QFontMetrics(self._f_label), QFontMetrics(self._f_value)

        self._tween = SizeTween(self)
        self._tween.valueChanged.connect(self._set_size)
        self._fade = Fade(self)
        self._fade.valueChanged.connect(self._on_fade)

        def make_timer(interval: int, slot, single: bool = False) -> QTimer:
            t = QTimer(self)
            t.setSingleShot(single)
            if interval:
                t.setInterval(interval)
            t.timeout.connect(slot)
            return t

        self._bars_timer = make_timer(33, self._tick_bars)
        self._second_timer = make_timer(1000, self.update)
        self._topmost_timer = make_timer(2000, self._keep_on_top)
        self._toast_timer = make_timer(0, self._end_toast, single=True)
        self._collapse_timer = make_timer(0, self.collapse, single=True)
        self._expire_timer = make_timer(0, self._on_music_expired, single=True)
        self._footer_timer = make_timer(0, self._clear_footer, single=True)

        settings.changed.connect(self._on_setting)
        sampler.updated.connect(self._on_snapshot)
        music.state_changed.connect(self._on_music)
        app = QGuiApplication.instance()
        if app is not None:
            app.primaryScreenChanged.connect(lambda *_: self._reposition())  # type: ignore[attr-defined]
        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            screen.geometryChanged.connect(lambda *_: self._reposition())

        self._retarget(animate=False)
        self.setWindowOpacity(float(settings.get("island_opacity")))

    # ------------------------------------------------------------------ вспомогательное
    def _accent(self) -> QColor:
        return QColor(str(self._settings.get("accent")))

    def _scale(self) -> float:
        return float(self._settings.get("island_scale"))

    def _metrics(self) -> list[str]:
        return [m for m in self._settings.get("island_metrics") if m in ISLAND_METRICS]

    def _music_visible(self) -> bool:
        m = self._music
        if m is None or not m.active or not self._settings.get("island_music_mode"):
            return False
        if m.playing or self._paused_since is None:
            return True
        return time.monotonic() - self._paused_since < float(self._settings.get("island_music_pause_timeout_s"))

    def _launcher_enabled(self) -> bool:
        return self._library is not None and bool(self._settings.get("island_launcher_enabled"))

    def _pages(self) -> list[str]:
        pages = ["music", "stats"] if self._music_visible() else ["stats"]
        return [*pages, "apps"] if self._launcher_enabled() else pages

    def _current_view(self) -> str:
        if self._mode == "expanded":
            if self._page == "apps" and self._launcher_enabled():
                return "exp_apps"
            return "exp_music" if (self._page == "music" and self._music_visible()) else "exp_stats"
        if self._toast is not None:
            return "toast"
        return "compact_music" if self._music_visible() else "compact_metrics"

    def _chips(self) -> list[Chip]:
        return build_chips(self._snap, self._metrics())

    def _chip_width(self, chip: Chip) -> float:
        width = self._fm_value.horizontalAdvance(chip.widest)
        if chip.label:
            width += self._fm_label.horizontalAdvance(chip.label) + 5
        return float(width)

    def _chips_total(self, chips: list[Chip]) -> float:
        if not chips:
            return float(self._fm_value.horizontalAdvance("NOVA") + 16)
        return sum(self._chip_width(c) for c in chips) + CHIP_GAP * (len(chips) - 1)

    def _position_now(self) -> float:
        m = self._music
        if m is None:
            return 0.0
        pos = m.position + (time.monotonic() - self._music_recv if m.playing else 0.0)
        return min(max(pos, 0.0), m.duration) if m.duration > 0 else pos

    # ------------------------------------------------------------------ размеры и анимация
    def _target_size(self) -> tuple[float, float]:
        w0, h0 = float(self._settings.get("island_width")), float(self._settings.get("island_height"))
        sc = self._scale()
        view = self._view
        if view == "compact_metrics":
            return max(w0, self._chips_total(self._chips()) + 2 * COMPACT_PAD), h0
        if view == "compact_music":
            return max(w0 + 70, 290.0), h0
        if view == "toast" and self._toast is not None:
            n = self._toast
            base = max(h0 + 10, 52.0) if n.text else max(h0, 40.0)
            return max(w0 + 90, 320.0), base + (12.0 if n.progress is not None else 0.0)
        if view == "exp_music":
            return EXP_W * sc, EXP_MUSIC_H * sc
        if view == "exp_apps":
            return EXP_W * sc, lt.tab_height(len(self._tiles)) * sc
        return EXP_W * sc, EXP_STATS_H * sc

    def _retarget(self, animate: bool = True) -> None:
        """Пересчитывает вид и целевой размер; запускает анимацию, если что-то изменилось."""
        new_view = self._current_view()
        view_changed = new_view != self._view
        self._view = new_view
        target = self._target_size()
        if view_changed or target != self._target:
            self._target = target
            duration = int(BASE_DURATION_MS * animation_scale(str(self._settings.get("animations"))))
            if not animate or duration <= 0 or not self.isVisible():
                self._tween.stop()
                self._fade.stop()
                self._alpha = 1.0
                self._set_size(*target)
            else:
                springy = target[0] * target[1] > self._size[0] * self._size[1]
                self._tween.start(self._size, target, duration, springy)
                if view_changed:
                    self._alpha = 0.0
                    self._fade.start(int(duration * 0.85))
        self._update_timers()
        self._sync_sampler()
        self.update()

    def _set_size(self, w: float, h: float) -> None:
        self._size = (w, h)
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        geo = screen.geometry()
        x = geo.x() + geo.width() / 2 - w / 2 + float(self._settings.get("island_offset_x"))
        x = min(max(x, geo.x()), geo.x() + geo.width() - w)
        y = geo.y() + float(self._settings.get("island_offset_y"))
        self.setGeometry(round(x), round(y), max(round(w), 1), max(round(h), 1))

    def _reposition(self) -> None:
        self._set_size(*self._size)

    def _on_fade(self, value: float) -> None:
        self._alpha = value
        self.update()

    # ------------------------------------------------------------------ таймеры и сэмплер
    def _update_timers(self) -> None:
        visible, view = self.isVisible(), self._view
        music_view = view in ("compact_music", "exp_music")
        animate = animation_scale(str(self._settings.get("animations"))) > 0
        playing = bool(self._music and self._music.playing)
        need_bars = visible and music_view and animate and (playing or self._bars_level > 0.02)
        need_second = visible and (view in ("exp_music", "exp_stats")
                                   or (view == "compact_metrics" and "clock" in self._metrics()))
        for timer, need in ((self._bars_timer, need_bars), (self._second_timer, need_second),
                            (self._topmost_timer, visible)):
            if need and not timer.isActive():
                timer.start()
            elif not need and timer.isActive():
                timer.stop()

    def _sync_sampler(self) -> None:
        need = self.isVisible() and (self._view == "exp_stats" or (
            self._view == "compact_metrics" and any(m in SAMPLED_METRICS for m in self._metrics())))
        if need:
            self._sampler.acquire("island", int(self._settings.get("island_update_ms")))
        else:
            self._sampler.release("island")

    def _tick_bars(self) -> None:
        target = 1.0 if (self._music and self._music.playing) else 0.0
        self._bars_level += (target - self._bars_level) * 0.15
        if target == 0.0 and self._bars_level < 0.02:
            self._bars_level = 0.0
            self._update_timers()
        self.update()

    def _keep_on_top(self) -> None:
        if self.isVisible():
            winapi.set_topmost(int(self.winId()))

    # ------------------------------------------------------------------ события данных
    def _on_snapshot(self, snap: Snapshot) -> None:
        self._snap = snap
        if self.isVisible() and (self._view in ("compact_metrics", "exp_stats")):
            self.update()

    def _on_music(self, state: MusicState) -> None:
        prev, now = self._music, time.monotonic()
        if not state.active:
            self._music, self._paused_since, self._pix, self._cover_cache = None, None, None, None
            self._expire_timer.stop()
        else:
            if state.playing:
                self._paused_since = None
                self._expire_timer.stop()
            elif prev is None or prev.playing or self._paused_since is None:
                self._paused_since = now
                timeout = int(float(self._settings.get("island_music_pause_timeout_s")) * 1000) + 100
                self._expire_timer.start(timeout)
            track_changed = prev is None or prev.track_key != state.track_key
            if track_changed:
                self._pix, self._cover_cache = None, None
                if state.thumbnail:
                    pix = QPixmap()
                    if pix.loadFromData(state.thumbnail):
                        self._pix = pix
            self._music, self._music_recv = state, now
            if (track_changed and prev is not None and state.playing
                    and self._settings.get("island_notify_music") and not self._settings.get("island_music_mode")):
                self.show_notification(Notification("music", state.title, state.artist))
        if self._mode == "expanded" and self._page == "music" and not self._music_visible():
            self._page = "stats"
        self._retarget()

    def _on_music_expired(self) -> None:
        self._retarget()

    def show_notification(self, n: Notification) -> None:
        """Короткий тост в капсуле. В раскрытом состоянии и при скрытом островке не показывается."""
        if not self.isVisible() or self._mode != "compact":
            return
        self._toast = n
        self._toast_timer.start(n.duration_ms)
        self._retarget()
        self.update()

    def _end_toast(self) -> None:
        self._toast = None
        self._retarget()

    # ------------------------------------------------------------------ видимость / настройки
    def on_fullscreen(self, active: bool, _name: str = "") -> None:
        self._fullscreen_active = bool(active)
        self._game_hidden = self._fullscreen_active and bool(self._settings.get("island_hide_in_fullscreen"))
        self.refresh_visibility()

    def refresh_visibility(self) -> None:
        want = bool(self._settings.get("island_enabled")) and not self._game_hidden
        if want and not self.isVisible():
            self._mode, self._toast = "compact", None
            self._retarget(animate=False)
            self.setWindowOpacity(float(self._settings.get("island_opacity")))
            self.show()
            winapi.set_topmost(int(self.winId()))
            self._music_ctl.set_active(True)
        elif not want and self.isVisible():
            self.hide()
            for timer in (self._toast_timer, self._collapse_timer):
                timer.stop()
            self._music_ctl.set_active(False)
        self._update_timers()
        self._sync_sampler()

    def _on_setting(self, key: str) -> None:
        if key == "island_enabled":
            self.refresh_visibility()
        elif key == "island_hide_in_fullscreen":
            self.on_fullscreen(self._fullscreen_active)
        elif key == "island_opacity":
            self.setWindowOpacity(float(self._settings.get("island_opacity")))
        elif key == "island_update_ms":
            self._sync_sampler()
        elif key == "island_launch_items":
            self._refresh_tiles()
        elif key in LAYOUT_KEYS:
            if self._page == "apps" and not self._launcher_enabled():
                self._page = "stats"
            self._retarget()
        elif key in ("accent", "island_notify_usb", "island_notify_volume", "island_notify_apps"):
            self.update()

    # ------------------------------------------------------------------ раскрытие / сворачивание
    def expand(self) -> None:
        if self._mode == "expanded" or not self.isVisible():
            return
        self._toast_timer.stop()
        self._toast, self._mode = None, "expanded"
        self._page = "music" if self._music_visible() else "stats"
        self._retarget()

    def collapse(self) -> None:
        self._collapse_timer.stop()
        if self._mode == "compact":
            return
        self._mode = "compact"
        self._retarget()

    def toggle(self) -> None:
        self.collapse() if self._mode == "expanded" else self.expand()

    def _set_page(self, page: str) -> None:
        if page != self._page and page in self._pages():
            self._page = page
            self._retarget()

    # ------------------------------------------------------------------ мышь
    def _to_design(self, pos: QPointF) -> QPointF:
        ox, oy, sc = self._xform
        return QPointF((pos.x() - ox) / sc, (pos.y() - oy) / sc)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.RightButton:
            if self._mode == "expanded":
                self.collapse()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if self._view in ("exp_music", "exp_stats", "exp_apps"):
            pt = self._to_design(event.position())
            for page, rect in self._dots:
                if rect.contains(pt):
                    self._set_page(page)
                    return
            if self._view == "exp_apps":
                slot = lt.hit_slot(len(self._tiles), pt.x(), pt.y())
                if slot is not None:
                    self._on_tile_clicked(slot)
                    return
            if self._view == "exp_music" and self._music is not None:
                if self._hit.get("play", QRectF()).contains(pt):
                    self._music = replace(self._music, playing=not self._music.playing)  # мгновенный отклик
                    self._music_recv = time.monotonic()
                    self._music_ctl.play_pause()
                    self._update_timers()
                    self.update()
                    return
                if self._hit.get("prev", QRectF()).contains(pt):
                    if self._music.can_prev:
                        self._music_ctl.previous_track()
                    return
                if self._hit.get("next", QRectF()).contains(pt):
                    if self._music.can_next:
                        self._music_ctl.next_track()
                    return
            self.collapse()
        elif self._view == "toast":
            self._toast_timer.stop()
            self._end_toast()
        else:
            self.expand()

    def wheelEvent(self, event) -> None:  # noqa: N802
        pages = self._pages()
        if self._mode == "expanded" and len(pages) > 1:
            idx = pages.index(self._page) if self._page in pages else 0
            step = -1 if event.angleDelta().y() > 0 else 1
            self._set_page(pages[(idx + step) % len(pages)])
        event.accept()

    def enterEvent(self, event) -> None:  # noqa: N802
        self._collapse_timer.stop()
        super().enterEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._view == "exp_apps":
            pt = self._to_design(event.position())
            slot = lt.hit_slot(len(self._tiles), pt.x(), pt.y())
            if slot != self._hover_slot:
                self._hover_slot = slot
                self.setCursor(Qt.CursorShape.PointingHandCursor if slot is not None else Qt.CursorShape.ArrowCursor)
                self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        if self._hover_slot is not None:
            self._hover_slot = None
            self.setCursor(Qt.CursorShape.ArrowCursor)
            self.update()
        seconds = int(self._settings.get("island_autocollapse_s"))
        if self._mode == "expanded" and seconds > 0:
            self._collapse_timer.start(seconds * 1000)
        super().leaveEvent(event)

    # ------------------------------------------------------------------ отрисовка
    def paintEvent(self, _event) -> None:  # noqa: N802
        w, h = float(self.width()), float(self.height())
        p = QPainter(self)
        p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing
                         | QPainter.RenderHint.SmoothPixmapTransform)
        radius = min(h / 2, MAX_RADIUS * self._scale())
        body = QRectF(0.5, 0.5, w - 1, h - 1)
        path = QPainterPath()
        path.addRoundedRect(body, radius, radius)
        p.fillPath(path, BG)
        p.setPen(QPen(EDGE, 1))
        p.drawPath(path)
        p.setClipPath(path)
        p.setOpacity(self._alpha)

        view, (cw, ch) = self._view, self._target
        if view.startswith("exp_"):
            sc = self._scale()
            ox = (w - cw) / 2
            p.translate(ox, 0)
            p.scale(sc, sc)
            self._xform = (ox, 0.0, sc)
            if view == "exp_music":
                self._paint_exp_music(p)
            elif view == "exp_apps":
                self._paint_exp_apps(p)
            else:
                self._paint_exp_stats(p)
        else:
            p.translate((w - cw) / 2, (h - ch) / 2)
            self._xform = ((w - cw) / 2, (h - ch) / 2, 1.0)
            if view == "compact_metrics":
                self._paint_metrics(p, cw, ch)
            elif view == "compact_music":
                self._paint_compact_music(p, cw, ch)
            elif view == "toast":
                self._paint_toast(p, cw, ch)
        p.end()

    def _paint_metrics(self, p: QPainter, w: float, h: float) -> None:
        chips = self._chips()
        x = (w - self._chips_total(chips)) / 2
        if not chips:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(self._accent())
            p.drawEllipse(QPointF(x + 4, h / 2), 3, 3)
            _text(p, QRectF(x + 14, 0, 80, h), "NOVA", self._f_value, TEXT, elide=False)
            return
        for chip in chips:
            cw = self._chip_width(chip)
            if chip.label:
                lw = self._fm_label.horizontalAdvance(chip.label)
                _text(p, QRectF(x, 0, lw + 2, h), chip.label, self._f_label, MUTED, elide=False)
                dim = chip.value == NA
                _text(p, QRectF(x + lw + 5, 0, cw - lw - 3, h), chip.value, self._f_value,
                      MUTED if dim else TEXT, elide=False)
            else:
                _text(p, QRectF(x, 0, cw + 2, h), chip.value, self._f_value, TEXT, elide=False)
            x += cw + CHIP_GAP

    def _paint_compact_music(self, p: QPainter, w: float, h: float) -> None:
        m = self._music
        if m is None:
            return
        cs = h - 14
        cover = QRectF(12, (h - cs) / 2, cs, cs)
        self._draw_cover(p, cover, min(8.0, cs / 3))
        bars_w = 5 * 3 + 4 * 3
        self._draw_bars(p, QRectF(w - 18 - bars_w, 0, bars_w, h), 5, 3, 3, h * 0.46)
        tx, avail = cover.right() + 10, (w - 18 - bars_w - 12) - (cover.right() + 10)
        title_w = min(float(QFontMetrics(self._f_value).horizontalAdvance(m.title)), avail)
        _text(p, QRectF(tx, 0, title_w, h), m.title, self._f_value, TEXT)
        if m.artist and avail - title_w > 48:
            _text(p, QRectF(tx + title_w + 6, 0, avail - title_w - 6, h), "· " + m.artist, self._f_label, MUTED)

    def _paint_toast(self, p: QPainter, w: float, h: float) -> None:
        n = self._toast
        if n is None:
            return
        area_h = h - (12.0 if n.progress is not None else 0.0)
        d = 28.0
        _draw_kind_icon(p, n.kind, 16 + d / 2, area_h / 2, d, self._accent(), muted=(n.kind == "volume" and n.progress == 0))
        tx = 16 + d + 12
        avail = w - tx - 16
        if n.text:
            _text(p, QRectF(tx, area_h / 2 - 19, avail, 20), n.title, _font(13, QFont.Weight.DemiBold), TEXT)
            _text(p, QRectF(tx, area_h / 2 + 1, avail, 17), n.text, _font(12), MUTED)
        else:
            _text(p, QRectF(tx, 0, avail, area_h), n.title, _font(13, QFont.Weight.DemiBold), TEXT)
        if n.progress is not None:
            track = QRectF(tx, h - 13, avail, 4)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255, 30))
            p.drawRoundedRect(track, 2, 2)
            p.setBrush(self._accent())
            p.drawRoundedRect(QRectF(track.x(), track.y(), track.width() * min(max(n.progress, 0.0), 1.0), 4), 2, 2)

    def _paint_exp_music(self, p: QPainter) -> None:
        m = self._music
        if m is None:
            return
        self._hit = {}
        self._draw_cover(p, QRectF(24, 22, 72, 72), 16)
        _text(p, QRectF(112, 28, 270, 24), m.title, self._f_title, TEXT)
        _text(p, QRectF(112, 53, 270, 22), m.artist or m.album, self._f_artist, MUTED)
        self._draw_bars(p, QRectF(398, 22, 42, 30), 7, 3, 3, 26)

        if m.duration > 0:
            pos = self._position_now()
            _text(p, QRectF(24, 105, 46, 22), fmt_clock(pos), self._f_small, MUTED, elide=False)
            _text(p, QRectF(412, 105, 48, 22), "-" + fmt_clock(max(m.duration - pos, 0)), self._f_small, MUTED,
                  ALIGN_R, elide=False)
            track = QRectF(76, 114, 330, 4)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255, 30))
            p.drawRoundedRect(track, 2, 2)
            p.setBrush(self._accent())
            p.drawRoundedRect(QRectF(track.x(), track.y(), track.width() * (pos / m.duration), 4), 2, 2)

        cy = 156.0
        for key, cx, r, glyph, enabled in (
                ("prev", 176.0, 22.0, "prev", m.can_prev),
                ("play", 240.0, 28.0, "pause" if m.playing else "play", True),
                ("next", 304.0, 22.0, "next", m.can_next)):
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255, 30 if key == "play" else 18))
            p.drawEllipse(QPointF(cx, cy), r, r)
            _draw_transport(p, glyph, cx, cy, 15 if key != "play" else 19, TEXT, enabled)
            self._hit[key] = QRectF(cx - r, cy - r, 2 * r, 2 * r)
        self._paint_dots(p, EXP_MUSIC_H)

    def _paint_exp_stats(self, p: QPainter) -> None:
        tiles = build_tiles(self._snap)
        gap, x0, y0, th = 12.0, 24.0, 18.0, 76.0
        tw = (416.0 - 2 * gap) / 3
        accent = self._accent()
        for i, tile in enumerate(tiles):
            r, c = divmod(i, 3)
            rect = QRectF(x0 + c * (tw + gap), y0 + r * (th + 10), tw, th)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255, 13))
            p.drawRoundedRect(rect, 14, 14)
            inner = rect.adjusted(12, 0, -12, 0)
            _text(p, QRectF(inner.x(), rect.y() + 8, inner.width(), 14), tile.label, self._f_label, MUTED)
            value_font = _font(16 if tile.key == "net" else 20, QFont.Weight.DemiBold)
            _text(p, QRectF(inner.x(), rect.y() + 22, inner.width(), 26), tile.value, value_font,
                  MUTED if tile.value == NA else TEXT)
            if tile.extra:
                _text(p, QRectF(inner.x(), rect.y() + 49, inner.width(), 15), tile.extra, _font(11), MUTED)
            if tile.fraction is not None:
                bar = QRectF(inner.x(), rect.bottom() - 10, inner.width(), 3.5)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(255, 255, 255, 28))
                p.drawRoundedRect(bar, 1.75, 1.75)
                p.setBrush(_temp_color(tile.fraction) if tile.warm else accent)
                p.drawRoundedRect(QRectF(bar.x(), bar.y(), bar.width() * min(max(tile.fraction, 0.0), 1.0), 3.5),
                                  1.75, 1.75)
        uptime = self._snap.uptime_s if self._snap else None
        _text(p, QRectF(x0, 184, 260, 18), f"Компьютер работает: {fmt_uptime(uptime)}", self._f_label, MUTED)
        _text(p, QRectF(x0 + 300, 184, 116, 18), time.strftime("%H:%M"), self._f_label, MUTED, ALIGN_R)
        self._hit = {}
        self._paint_dots(p, EXP_STATS_H)

    def _paint_dots(self, p: QPainter, height: float) -> None:
        pages = self._pages()
        self._dots = []
        if len(pages) < 2:
            return
        cx, step = 466.0, 14.0
        top = height / 2 - step * (len(pages) - 1) / 2
        p.setPen(Qt.PenStyle.NoPen)
        for i, page in enumerate(pages):
            cy = top + i * step
            p.setBrush(_alpha(TEXT, 1.0 if page == self._page else 0.3))
            p.drawEllipse(QPointF(cx, cy), 3, 3)
            self._dots.append((page, QRectF(cx - 8, cy - 8, 16, 16)))

    def _draw_bars(self, p: QPainter, rect: QRectF, n: int, bw: float, gap: float, max_h: float) -> None:
        animate = animation_scale(str(self._settings.get("animations"))) > 0
        playing = bool(self._music and self._music.playing)
        level = self._bars_level if animate else (1.0 if playing else 0.0)
        heights = bar_heights(time.monotonic() if animate else 0.0, level, n)
        total = n * bw + (n - 1) * gap
        x, cy = rect.right() - total, rect.center().y()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(_alpha(self._accent(), 0.85))
        for i, hh in enumerate(heights):
            bh = max(3.0, hh * max_h)
            p.drawRoundedRect(QRectF(x + i * (bw + gap), cy - bh / 2, bw, bh), bw / 2, bw / 2)

    def _scaled_cover(self, size: QRectF, factor: float) -> QPixmap | None:
        if self._pix is None:
            return None
        key = (size.width(), size.height(), factor)
        if self._cover_cache is not None and self._cover_cache[0] == key:
            return self._cover_cache[1]
        side_w, side_h = max(int(size.width() * factor), 1), max(int(size.height() * factor), 1)
        scaled = self._pix.scaled(side_w, side_h, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                  Qt.TransformationMode.SmoothTransformation)
        scaled.setDevicePixelRatio(factor)
        self._cover_cache = (key, scaled)
        return scaled

    def _draw_cover(self, p: QPainter, rect: QRectF, radius: float) -> None:
        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        factor = self.devicePixelRatioF() * (self._xform[2] if self._view.startswith("exp_") else 1.0)
        pix = self._scaled_cover(rect, factor)
        p.save()
        if pix is not None:
            p.setClipPath(path, Qt.ClipOperation.IntersectClip)
            lw, lh = pix.width() / factor, pix.height() / factor
            p.drawPixmap(QPointF(rect.x() + (rect.width() - lw) / 2, rect.y() + (rect.height() - lh) / 2), pix)
        else:
            p.fillPath(path, QColor(255, 255, 255, 22))
            p.setFont(_font(int(rect.height() * 0.5), QFont.Weight.DemiBold))
            p.setPen(MUTED)
            p.drawText(rect, ALIGN_C, "♪")
        p.restore()

    # ------------------------------------------------------------------ вкладка «Приложения»
    def attach_launcher(self, library: LibraryManager, icons: IconCache) -> None:
        """Подключает библиотеку: после этого в раскрытом острове появляется вкладка «Приложения»."""
        self._library, self._icons = library, icons
        library.changed.connect(self._refresh_tiles)
        self._refresh_tiles()

    def _refresh_tiles(self) -> None:
        if self._library is None or self._icons is None:
            return
        tiles: list[tuple[str, int, str, QPixmap]] = []
        for kind, item_id in self._library.pinned():
            item = self._library.table(kind).get(item_id)
            if item is not None:
                tiles.append((kind, item_id, item.title, self._icons.pixmap(kind, item, lt.ICON)))
        self._tiles = tiles
        if self._hover_slot is not None and self._hover_slot >= lt.slot_count(len(tiles)):
            self._hover_slot = None
        if self._page == "apps" and not self._launcher_enabled():
            self._page = "stats"
        if self.isVisible():
            self._retarget()

    def _on_tile_clicked(self, slot: int) -> None:
        if self._library is None:
            return
        if slot >= len(self._tiles):                 # плитка «+»
            self.settings_requested.emit()
            self.collapse()
            return
        kind, item_id, _title, _pix = self._tiles[slot]
        result = self._library.launch(kind, item_id)
        if result is not None and result.ok:
            self.collapse()

    def _show_footer(self, text: str, error: bool = False) -> None:
        self._footer_msg = (text, error)
        self._footer_timer.start(4000)
        self.update()

    def _clear_footer(self) -> None:
        self._footer_msg = None
        self.update()

    @staticmethod
    def _dropped_paths(event) -> list[str]:  # noqa: ANN001
        mime = event.mimeData()
        if mime is None or not mime.hasUrls():
            return []
        paths = [u.toLocalFile() for u in mime.urls() if u.isLocalFile()]
        return [p for p in paths if p.lower().endswith(DROP_SUFFIXES)]

    def dragEnterEvent(self, event) -> None:  # noqa: N802, ANN001
        if self._launcher_enabled() and self._dropped_paths(event):
            event.acceptProposedAction()
            if self._mode != "expanded":
                self.expand()
                self._drag_expanded = True
            self._set_page("apps")
            self._collapse_timer.stop()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:  # noqa: N802, ANN001
        event.acceptProposedAction()

    def dragLeaveEvent(self, event) -> None:  # noqa: N802, ANN001
        if self._drag_expanded:
            self._drag_expanded = False
            self.collapse()
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:  # noqa: N802, ANN001
        self._drag_expanded = False
        if self._library is None:
            event.ignore()
            return
        messages, failed = [], False
        for path in self._dropped_paths(event):
            ok, message = self._library.add_to_island(path)
            messages.append(message)
            failed = failed or not ok
        event.acceptProposedAction()
        self._set_page("apps")
        if messages:
            self._show_footer(" ".join(messages[:2]), error=failed)
        self._refresh_tiles()

    def _paint_exp_apps(self, p: QPainter) -> None:
        self._hit = {}
        count = len(self._tiles)
        rects = lt.slot_rects(count)
        for i, (x, y, w, h) in enumerate(rects):
            rect = QRectF(x, y, w, h)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255, 30 if i == self._hover_slot else 13))
            p.drawRoundedRect(rect, 14, 14)
            if i < count:
                _kind, _id, title, pix = self._tiles[i]
                p.drawPixmap(QRectF(x + (w - lt.ICON) / 2, y + 9, lt.ICON, lt.ICON), pix, QRectF(pix.rect()))
                _text(p, QRectF(x + 4, y + lt.ICON + 15, w - 8, 18), title, _font(11, QFont.Weight.Medium), TEXT, ALIGN_C)
            else:                                       # плитка «+»
                cx, cy = x + w / 2, y + 9 + lt.ICON / 2
                p.setPen(QPen(_alpha(TEXT, 0.55), 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                p.drawLine(QPointF(cx - 8, cy), QPointF(cx + 8, cy))
                p.drawLine(QPointF(cx, cy - 8), QPointF(cx, cy + 8))
                _text(p, QRectF(x + 4, y + lt.ICON + 15, w - 8, 18), "Добавить", _font(11, QFont.Weight.Medium), MUTED, ALIGN_C)
        height = lt.tab_height(count)
        if self._footer_msg is not None:
            text, error = self._footer_msg
            color = QColor(248, 113, 113) if error else self._accent()
        else:
            text, color = ("Перетащите .exe или ярлык на остров — программа появится здесь"
                           if count < lt.MAX_ITEMS else "Закреплено максимум программ"), MUTED
        _text(p, QRectF(lt.X0, height - lt.FOOTER_H + 4, lt.CONTENT_W - 10, 20), text, self._f_label, color)
        self._paint_dots(p, height)

    # ------------------------------------------------------------------ завершение
    def shutdown(self) -> None:
        for timer in (self._bars_timer, self._second_timer, self._topmost_timer, self._toast_timer,
                      self._collapse_timer, self._expire_timer, self._footer_timer):
            timer.stop()
        self._tween.stop()
        self._fade.stop()
        self._sampler.release("island")
        self.hide()
