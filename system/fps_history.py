"""Локальная история FPS-сессий. Хранится в AppData, отдельно от каталога установки."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


class FpsHistoryStore:
    MAX_SESSIONS = 120
    MAX_POINTS = 1800

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._sessions: list[dict[str, Any]] = []
        self._active_id: int | None = None
        self._last_save = 0.0
        self._load()

    def _load(self) -> None:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(value, list):
                self._sessions = [x for x in value if isinstance(x, dict)][-self.MAX_SESSIONS:]
        except (OSError, ValueError, TypeError):
            self._sessions = []

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(self.path.suffix + ".tmp")
        temp.write_text(json.dumps(self._sessions[-self.MAX_SESSIONS:], ensure_ascii=False, indent=2),
                        encoding="utf-8")
        temp.replace(self.path)
        self._last_save = time.monotonic()

    def record(self, game: str, data: dict[str, Any], now: float | None = None) -> None:
        game = str(game or "Неизвестная игра").strip()[:120]
        if not game or int(data.get("fps", 0) or 0) <= 0:
            return
        now = time.time() if now is None else float(now)
        current = self._active()
        if current is None or str(current.get("game", "")).casefold() != game.casefold():
            self.finish(now)
            current = {
                "id": int(now * 1000), "game": game, "started_at": now, "ended_at": None,
                "ticks": 0, "avg_sum": 0.0, "peak_fps": 0, "low_1_min": None,
                "low_01_min": None, "frametime_sum": 0.0, "points": [],
            }
            self._sessions.append(current)
            self._active_id = int(current["id"])
        fps = max(0, int(data.get("fps", 0) or 0))
        avg = max(0, int(data.get("avg", 0) or 0))
        low_1 = max(0, int(data.get("low_1", data.get("low", 0)) or 0))
        low_01 = max(0, int(data.get("low_01", 0) or 0))
        frame_ms = max(0.0, float(data.get("frame_ms", 0.0) or 0.0))
        current["ticks"] = int(current.get("ticks", 0)) + 1
        current["avg_sum"] = float(current.get("avg_sum", 0)) + (avg or fps)
        current["peak_fps"] = max(int(current.get("peak_fps", 0)), fps)
        current["low_1_min"] = low_1 if current.get("low_1_min") is None else min(int(current["low_1_min"]), low_1)
        if low_01:
            current["low_01_min"] = low_01 if current.get("low_01_min") is None else min(int(current["low_01_min"]), low_01)
        current["frametime_sum"] = float(current.get("frametime_sum", 0)) + frame_ms
        points = current.setdefault("points", [])
        # Store one point per ~2 seconds, bounded to avoid unbounded AppData growth.
        if not points or now - float(points[-1][0]) >= 2.0:
            points.append([now, fps, frame_ms])
            del points[:-self.MAX_POINTS]
        if time.monotonic() - self._last_save >= 5.0:
            self._save()

    def finish(self, now: float | None = None) -> None:
        current = self._active()
        if current is None:
            return
        now = time.time() if now is None else float(now)
        current["ended_at"] = now
        ticks = max(1, int(current.get("ticks", 0)))
        current["avg_fps"] = round(float(current.get("avg_sum", 0)) / ticks)
        current["avg_frametime"] = round(float(current.get("frametime_sum", 0)) / ticks, 2)
        current["duration_s"] = max(0, int(now - float(current.get("started_at", now))))
        current.pop("avg_sum", None)
        current.pop("frametime_sum", None)
        self._active_id = None
        self._sessions = self._sessions[-self.MAX_SESSIONS:]
        self._save()

    def _active(self) -> dict[str, Any] | None:
        if self._active_id is None:
            return None
        return next((x for x in reversed(self._sessions) if x.get("id") == self._active_id), None)

    def sessions(self, limit: int = 20) -> list[dict[str, Any]]:
        return [dict(x) for x in reversed(self._sessions[-max(1, limit):])]

    def clear(self) -> None:
        self._sessions.clear()
        self._active_id = None
        self._save()
