"""SQLite-хранилище NOVA: настройки, библиотека игр и приложений, история запусков.

Одно соединение на всё приложение (check_same_thread=False) под RLock — запросы мелкие,
а потоки сканирования/мониторинга могут писать параллельно с интерфейсом.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Generic, TypeVar
from collections.abc import Iterable

from .models import AppEntry, Game, ITEM_COLUMNS, LibraryItem

log = logging.getLogger(__name__)

SCHEMA_VERSION = 2

# Колонки, добавленные в версии 2 (имя, DDL). Применяются через ALTER TABLE, поэтому
# существующие библиотеки пользователей переносятся без потерь.
_V2_COLUMNS = (
    ("install_dir", "TEXT NOT NULL DEFAULT ''"),
    ("args", "TEXT NOT NULL DEFAULT ''"),
    ("options", "TEXT NOT NULL DEFAULT '{}'"),
    ("hidden", "INTEGER NOT NULL DEFAULT 0"),
)

_ITEM_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS {table} (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    exe_path    TEXT NOT NULL DEFAULT '',
    launch_uri  TEXT NOT NULL DEFAULT '',
    source      TEXT NOT NULL DEFAULT 'manual',
    external_id TEXT,
    image_path  TEXT NOT NULL DEFAULT '',
    category    TEXT NOT NULL DEFAULT '',
    favorite    INTEGER NOT NULL DEFAULT 0,
    play_count  INTEGER NOT NULL DEFAULT 0,
    last_played REAL,
    created_at  REAL NOT NULL,
    UNIQUE (source, external_id)
);
CREATE INDEX IF NOT EXISTS idx_{table}_last_played ON {table}(last_played);
CREATE INDEX IF NOT EXISTS idx_{table}_favorite ON {table}(favorite);
"""

T = TypeVar("T", bound=LibraryItem)


class ItemTable(Generic[T]):
    """CRUD для таблицы игр или приложений. Удаление затрагивает только запись в базе."""

    def __init__(self, db: Database, table: str, kind: str, model: type[T]) -> None:
        self._db, self._table, self._kind, self._model = db, table, kind, model

    # -- создание / чтение --------------------------------------------------
    def add(self, **fields: Any) -> int:
        data = self._clean(fields)
        if not data.get("title"):
            raise ValueError("title обязателен")
        data.setdefault("created_at", time.time())
        cols = ", ".join(data)
        marks = ", ".join("?" for _ in data)
        cur = self._db.execute(f"INSERT INTO {self._table} ({cols}) VALUES ({marks})", tuple(data.values()))
        return int(cur.lastrowid or 0)

    def get(self, item_id: int) -> T | None:
        row = self._db.query_one(f"SELECT * FROM {self._table} WHERE id = ?", (item_id,))
        return self._model.from_row(row) if row else None

    def find_by_external(self, source: str, external_id: str) -> T | None:
        row = self._db.query_one(
            f"SELECT * FROM {self._table} WHERE source = ? AND external_id = ?", (source, external_id))
        return self._model.from_row(row) if row else None

    def find_by_exe(self, exe_path: str) -> T | None:
        """Запись с таким же путём к EXE (без учёта регистра — как в Windows). Скрытые тоже ищутся."""
        if not exe_path:
            return None
        row = self._db.query_one(
            f"SELECT * FROM {self._table} WHERE exe_path = ? COLLATE NOCASE LIMIT 1", (exe_path,))
        return self._model.from_row(row) if row else None

    def items(self, search: str = "", category: str | None = None, favorites_only: bool = False,
              include_hidden: bool = False) -> list[T]:
        sql = f"SELECT * FROM {self._table} WHERE 1=1"
        params: list[Any] = []
        if not include_hidden:
            sql += " AND hidden = 0"
        if search.strip():
            sql += " AND title LIKE ? ESCAPE '\\'"
            escaped = search.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            params.append(f"%{escaped}%")
        if category:
            sql += " AND category = ?"
            params.append(category)
        if favorites_only:
            sql += " AND favorite = 1"
        sql += " ORDER BY title COLLATE NOCASE"
        return [self._model.from_row(r) for r in self._db.query_all(sql, tuple(params))]

    def recent(self, limit: int = 8) -> list[T]:
        rows = self._db.query_all(
            f"SELECT * FROM {self._table} WHERE last_played IS NOT NULL AND hidden = 0 "
            "ORDER BY last_played DESC LIMIT ?", (limit,))
        return [self._model.from_row(r) for r in rows]

    def favorites(self, limit: int = 12) -> list[T]:
        rows = self._db.query_all(
            f"SELECT * FROM {self._table} WHERE favorite = 1 AND hidden = 0 "
            "ORDER BY title COLLATE NOCASE LIMIT ?", (limit,))
        return [self._model.from_row(r) for r in rows]

    def categories(self) -> list[str]:
        rows = self._db.query_all(
            f"SELECT DISTINCT category FROM {self._table} WHERE category <> '' AND hidden = 0 "
            "ORDER BY category COLLATE NOCASE")
        return [r["category"] for r in rows]

    def hidden_items(self) -> list[T]:
        rows = self._db.query_all(f"SELECT * FROM {self._table} WHERE hidden = 1 ORDER BY title COLLATE NOCASE")
        return [self._model.from_row(r) for r in rows]

    def count(self, include_hidden: bool = False) -> int:
        sql = f"SELECT COUNT(*) AS n FROM {self._table}"
        if not include_hidden:
            sql += " WHERE hidden = 0"
        row = self._db.query_one(sql)
        return int(row["n"]) if row else 0

    # -- изменение ---------------------------------------------------------
    def update(self, item_id: int, **fields: Any) -> None:
        data = self._clean(fields)
        if not data:
            return
        sets = ", ".join(f"{k} = ?" for k in data)
        self._db.execute(f"UPDATE {self._table} SET {sets} WHERE id = ?", (*data.values(), item_id))

    def delete(self, item_id: int) -> None:
        """Убирает карточку из библиотеки. Файлы на диске не трогаются."""
        self._db.execute(f"DELETE FROM {self._table} WHERE id = ?", (item_id,))
        self._db.execute("DELETE FROM launch_history WHERE kind = ? AND item_id = ?", (self._kind, item_id))

    def set_hidden(self, item_id: int, hidden: bool) -> None:
        """Прячет карточку из библиотеки, но помнит о ней — автопоиск не добавит её снова."""
        self._db.execute(f"UPDATE {self._table} SET hidden = ? WHERE id = ?", (1 if hidden else 0, item_id))

    def set_option(self, item_id: int, key: str, value: Any) -> None:
        """Меняет один ключ в JSON-настройках записи (None — удалить ключ)."""
        with self._db.lock:
            item = self.get(item_id)
            if item is None:
                return
            opts = item.opts
            if value is None:
                opts.pop(key, None)
            else:
                opts[key] = value
            self.update(item_id, options=json.dumps(opts, ensure_ascii=False))

    def record_launch(self, item_id: int) -> int:
        """Фиксирует запуск; возвращает id записи истории, чтобы потом проставить время окончания."""
        now = time.time()
        self._db.execute(
            f"UPDATE {self._table} SET play_count = play_count + 1, last_played = ? WHERE id = ?", (now, item_id))
        cur = self._db.execute(
            "INSERT INTO launch_history (kind, item_id, started_at) VALUES (?, ?, ?)", (self._kind, item_id, now))
        return int(cur.lastrowid or 0)

    def finish_launch(self, history_id: int, ended_at: float | None = None) -> None:
        """Проставляет время окончания сессии (только если оно ещё не записано)."""
        if history_id <= 0:
            return
        self._db.execute(
            "UPDATE launch_history SET ended_at = ? WHERE id = ? AND ended_at IS NULL",
            (ended_at if ended_at is not None else time.time(), history_id))

    def playtime(self, item_id: int) -> float:
        """Суммарное время в секундах по завершённым сессиям, отслеженным NOVA."""
        row = self._db.query_one(
            "SELECT COALESCE(SUM(ended_at - started_at), 0) AS total FROM launch_history "
            "WHERE kind = ? AND item_id = ? AND ended_at IS NOT NULL AND ended_at >= started_at",
            (self._kind, item_id))
        return float(row["total"]) if row else 0.0

    def history(self, item_id: int | None = None, limit: int = 50) -> list[dict[str, Any]]:
        sql = "SELECT * FROM launch_history WHERE kind = ?"
        params: list[Any] = [self._kind]
        if item_id is not None:
            sql += " AND item_id = ?"
            params.append(item_id)
        sql += " ORDER BY started_at DESC LIMIT ?"
        params.append(limit)
        return [dict(r) for r in self._db.query_all(sql, tuple(params))]

    @staticmethod
    def _clean(fields: dict[str, Any]) -> dict[str, Any]:
        unknown = set(fields) - set(ITEM_COLUMNS) - {"created_at"}
        if unknown:
            raise ValueError(f"неизвестные поля: {sorted(unknown)}")
        out = dict(fields)
        for flag in ("favorite", "hidden"):
            if flag in out:
                out[flag] = 1 if out[flag] else 0
        return out


class Database:
    def __init__(self, path: Path | str) -> None:
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode = WAL")
            self._conn.execute("PRAGMA synchronous = NORMAL")
        self._migrate()
        self.games: ItemTable[Game] = ItemTable(self, "games", "game", Game)
        self.apps: ItemTable[AppEntry] = ItemTable(self, "apps", "app", AppEntry)

    # -- низкий уровень ----------------------------------------------------
    @property
    def lock(self) -> threading.RLock:
        """Блокировка соединения: позволяет сделать «прочитать-изменить-записать» атомарно."""
        return self._lock

    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, tuple(params))
            self._conn.commit()
            return cur

    def query_all(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, tuple(params)).fetchall()

    def query_one(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(sql, tuple(params)).fetchone()

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except sqlite3.Error:
                log.exception("Ошибка закрытия базы")

    # -- миграции ----------------------------------------------------------
    def _migrate(self) -> None:
        with self._lock:
            version = self._conn.execute("PRAGMA user_version").fetchone()[0]
            if version < 1:
                script = (
                    "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);"
                    + _ITEM_TABLE_SQL.format(table="games")
                    + _ITEM_TABLE_SQL.format(table="apps")
                    + """
                    CREATE TABLE IF NOT EXISTS launch_history (
                        id         INTEGER PRIMARY KEY AUTOINCREMENT,
                        kind       TEXT NOT NULL,
                        item_id    INTEGER NOT NULL,
                        started_at REAL NOT NULL,
                        ended_at   REAL
                    );
                    CREATE INDEX IF NOT EXISTS idx_history_item ON launch_history(kind, item_id, started_at);
                    """
                )
                self._conn.executescript(script)
                self._conn.execute("PRAGMA user_version = 1")
                self._conn.commit()
            if version < 2:
                for table in ("games", "apps"):
                    existing = {r["name"] for r in self._conn.execute(f"PRAGMA table_info({table})")}
                    for name, ddl in _V2_COLUMNS:
                        if name not in existing:
                            self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
                    self._conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_hidden ON {table}(hidden)")
                self._conn.execute("PRAGMA user_version = 2")
                self._conn.commit()
                log.info("База данных обновлена до версии 2")

    # -- настройки ---------------------------------------------------------
    def get_setting(self, key: str, default: Any = None) -> Any:
        row = self.query_one("SELECT value FROM settings WHERE key = ?", (key,))
        if row is None:
            return default
        try:
            return json.loads(row["value"])
        except (TypeError, ValueError):
            log.warning("Повреждённое значение настройки %s — использую значение по умолчанию", key)
            return default

    def set_setting(self, key: str, value: Any) -> None:
        self.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value, ensure_ascii=False)))

    def set_settings(self, values: dict[str, Any]) -> None:
        """Пакетная запись одной транзакцией."""
        with self._lock:
            self._conn.executemany(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                [(k, json.dumps(v, ensure_ascii=False)) for k, v in values.items()])
            self._conn.commit()

    def all_settings(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for row in self.query_all("SELECT key, value FROM settings"):
            try:
                out[row["key"]] = json.loads(row["value"])
            except (TypeError, ValueError):
                continue
        return out

    def reset_settings(self) -> None:
        """Сбрасывает только настройки. Библиотека, история и служебное состояние («state.*» —
        например, что вернуть после игры) не затрагиваются."""
        self.execute("DELETE FROM settings WHERE key NOT LIKE 'state.%'")


class StateStore:
    """Служебное состояние NOVA в той же базе (ключи «state.*»). Потокобезопасно и не сбрасывается кнопкой
    «Сбросить настройки»: здесь лежит то, что нужно вернуть после игры, и исходные значения реестра."""

    PREFIX = "state."

    def __init__(self, db: Database) -> None:
        self._db = db

    def get(self, key: str, default: Any = None) -> Any:
        return self._db.get_setting(self.PREFIX + key, default)

    def set(self, key: str, value: Any) -> None:
        self._db.set_setting(self.PREFIX + key, value)
