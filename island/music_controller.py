"""Музыка через Windows Media Session (GlobalSystemMediaTransportControls).

Работает с Spotify, браузерами и любыми приложениями, которые публикуют медиасессию Windows.
Опрос раз в секунду в отдельном потоке с собственным asyncio-циклом; обложка читается только при смене трека.
Пакеты: winrt-runtime, winrt-Windows.Media.Control, winrt-Windows.Storage.Streams,
winrt-Windows.Foundation, winrt-Windows.Foundation.Collections (см. requirements.txt).
"""
from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import replace
from typing import Any

from PySide6.QtCore import QObject, Signal

from island.model import MusicState

log = logging.getLogger(__name__)

try:
    from winrt.windows.media.control import (  # type: ignore[import-not-found,unused-ignore]
        GlobalSystemMediaTransportControlsSessionManager as _Manager,
        GlobalSystemMediaTransportControlsSessionPlaybackStatus as _Status,
    )
    from winrt.windows.storage.streams import DataReader as _DataReader  # type: ignore[import-not-found,unused-ignore]
    WINRT_AVAILABLE, WINRT_ERROR = True, ""
except Exception as _exc:  # не Windows или пакеты winrt не установлены
    _Manager = _Status = _DataReader = None
    WINRT_AVAILABLE, WINRT_ERROR = False, f"{type(_exc).__name__}: {_exc}"

POLL_INTERVAL_S = 1.0
IDLE_INTERVAL_S = 3.0
MAX_THUMBNAIL_BYTES = 8_000_000


class MusicController(QObject):
    state_changed = Signal(object)   # MusicState (title == "" означает «ничего не играет»)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop = threading.Event()
        self._active = True
        self._session: Any = None
        self._state = MusicState()
        self._thumb_key: tuple[str, str, str, str] | None = None
        self._thumb: bytes | None = None

    @property
    def available(self) -> bool:
        return WINRT_AVAILABLE

    @property
    def status(self) -> str:
        return "Windows Media Session подключён" if WINRT_AVAILABLE else f"недоступно ({WINRT_ERROR})"

    def set_active(self, active: bool) -> None:
        """Пока островок скрыт (например, в игре), медиасессии не опрашиваются."""
        self._active = active

    # -- жизненный цикл ----------------------------------------------------
    def start(self) -> None:
        if not WINRT_AVAILABLE or self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._thread_main, name="nova-music", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def _thread_main(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        try:
            loop.run_until_complete(self._run())
        except Exception:
            log.exception("Музыкальный контроллер остановлен из-за ошибки")
        finally:
            loop.close()
            self._loop = None

    async def _run(self) -> None:
        manager = await _Manager.request_async()
        while not self._stop.is_set():
            if not self._active:
                await asyncio.sleep(IDLE_INTERVAL_S)
                continue
            try:
                state = await self._read_state(manager)
            except Exception:
                log.debug("Сбой чтения медиасессии", exc_info=True)
                state = MusicState()
            if state != self._state:
                self._state = state
                self.state_changed.emit(state)
            await asyncio.sleep(POLL_INTERVAL_S)

    # -- чтение состояния ----------------------------------------------------
    @staticmethod
    def _pick_session(manager: Any) -> Any:
        sessions = list(manager.get_sessions())
        for session in sessions:
            if session.get_playback_info().playback_status == _Status.PLAYING:
                return session
        return manager.get_current_session() or (sessions[0] if sessions else None)

    async def _read_state(self, manager: Any) -> MusicState:
        session = self._pick_session(manager)
        self._session = session
        if session is None:
            return MusicState()
        props = await session.try_get_media_properties_async()
        if not props or not props.title:
            return MusicState()
        playback = session.get_playback_info()
        timeline = session.get_timeline_properties()
        controls = playback.controls
        try:
            duration = max((timeline.end_time - timeline.start_time).total_seconds(), 0.0)
            position = min(max((timeline.position - timeline.start_time).total_seconds(), 0.0), duration or 1e9)
        except Exception:
            duration, position = 0.0, 0.0
        base = MusicState(
            app_id=str(session.source_app_user_model_id or ""), title=str(props.title),
            artist=str(props.artist or ""), album=str(props.album_title or ""),
            playing=playback.playback_status == _Status.PLAYING,
            position=position, duration=duration,
            can_prev=bool(controls.is_previous_enabled), can_next=bool(controls.is_next_enabled))
        if base.track_key != self._thumb_key:
            self._thumb_key, self._thumb = base.track_key, await self._read_thumbnail(props)
        return replace(base, thumbnail=self._thumb)

    @staticmethod
    async def _read_thumbnail(props: Any) -> bytes | None:
        try:
            ref = props.thumbnail
            if ref is None:
                return None
            stream = await ref.open_read_async()
            size = int(stream.size)
            if not 0 < size <= MAX_THUMBNAIL_BYTES:
                return None
            reader = _DataReader(stream.get_input_stream_at(0))
            await reader.load_async(size)
            buffer = bytearray(size)
            reader.read_bytes(buffer)
            stream.close()
            return bytes(buffer)
        except Exception:
            log.debug("Не удалось прочитать обложку", exc_info=True)
            return None

    # -- управление ------------------------------------------------------------
    def play_pause(self) -> None:
        self._command("try_toggle_play_pause_async")

    def next_track(self) -> None:
        self._command("try_skip_next_async")

    def previous_track(self) -> None:
        self._command("try_skip_previous_async")

    def _command(self, method: str) -> None:
        session, loop = self._session, self._loop
        if session is None or loop is None:
            return

        async def run() -> None:
            try:
                await getattr(session, method)()
            except Exception:
                log.debug("Команда %s не выполнена", method, exc_info=True)

        asyncio.run_coroutine_threadsafe(run(), loop)
