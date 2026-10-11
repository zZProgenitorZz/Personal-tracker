import json
import sqlite3
from contextlib import contextmanager
import threading
import types
import typing
import weakref
from dataclasses import asdict, fields, is_dataclass
from datetime import date, datetime, time
from enum import Enum
from pathlib import Path


def _to_json(value):
    if isinstance(value, (datetime, date, time)):  # datetime first: it is also a date
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    raise TypeError(f"Can't store {type(value)}")


def _from_json(kind, value):
    """A JSON value back to the type from the dataclass: date, time, enum, nested
    dataclass, tuple, or optional (X | None). Everything else comes back as it was."""
    if value is None:
        return None
    if typing.get_origin(kind) in (typing.Union, types.UnionType):
        options = [k for k in typing.get_args(kind) if k is not type(None)]
        return _from_json(options[0], value) if len(options) == 1 else value
    if typing.get_origin(kind) is tuple:
        [item, *_] = typing.get_args(kind) or (object,)
        return tuple(_from_json(item, v) for v in value)
    if kind is datetime:
        return datetime.fromisoformat(value)
    if kind is date:
        return date.fromisoformat(value)
    if kind is time:
        return time.fromisoformat(value)
    if isinstance(kind, type) and issubclass(kind, Enum):
        return kind(value)
    if is_dataclass(kind):
        return kind(**{f.name: _from_json(f.type, value[f.name]) for f in fields(kind) if f.name in value})
    return value


class EventStore:
    def __init__(self, path: str, event_types: list[type]):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._subscribers = []
        self._types = {t.__name__: t for t in event_types}
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.RLock()  # writing, copying and restoring never overlap
        # Close the connection as soon as the store is cleaned up (or on close()).
        self._closer = weakref.finalize(self, self._conn.close)
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS events (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                stream_id TEXT NOT NULL,
                type      TEXT NOT NULL,
                data      TEXT NOT NULL,
                at        TEXT NOT NULL
            )"""
        )
        # load_stream looks up stream_id; without an index that gets slower as events grow.
        # IF NOT EXISTS: also safe for an existing database.
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_events_stream ON events(stream_id)")
        self._conn.commit()
        self._pending: list | None = None  # events of a running transaction(), not announced yet

    def close(self) -> None:
        self._closer()

    def subscribe(self, callback) -> None:
        self._subscribers.append(callback)

    def append(self, stream_id: str, events: list) -> None:
        with self._lock:
            rows = [(stream_id, type(event).__name__, json.dumps(asdict(event), default=_to_json), event.at.isoformat())
                    for event in events]
            if self._pending is not None:  # inside transaction(): saving happens at the end
                self._conn.executemany("INSERT INTO events (stream_id, type, data, at) VALUES (?, ?, ?, ?)", rows)
                self._pending.extend(events)
                return
            with self._conn:
                self._conn.executemany("INSERT INTO events (stream_id, type, data, at) VALUES (?, ?, ?, ?)", rows)
        self._notify(events)

    @contextmanager
    def transaction(self):
        """Saves many appends at once (an import, for example). Each command still goes
        through its aggregate: load_stream sees what was added inside the transaction. Only after
        saving do the subscribers hear about it; if anything goes wrong, nothing is saved."""
        with self._lock:
            if self._pending is not None:
                raise RuntimeError("A transaction is already running")
            self._pending = []
            try:
                yield
            except BaseException:
                self._conn.rollback()
                self._pending = None
                raise
            self._conn.commit()
            events, self._pending = self._pending, None
        self._notify(events)

    def _notify(self, events: list) -> None:
        for event in events:
            for callback in self._subscribers:
                callback(event)

    def count(self) -> int:
        with self._lock:
            return self._conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]

    # ---- Backup ----
    # Through SQLite's backup API: always a complete, consistent copy,
    # even when something is being written at the same moment.

    def copy_to(self, path) -> None:
        target = sqlite3.connect(str(path))
        try:
            with self._lock:
                self._conn.backup(target)
        finally:
            target.close()

    def restore_from(self, path) -> None:
        """Replaces all events with those from the copy at `path`."""
        source = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
        try:
            with self._lock:
                source.backup(self._conn)
        finally:
            source.close()

    def load_stream(self, stream_id: str) -> list:
        rows = self._conn.execute(
            "SELECT type, data FROM events WHERE stream_id = ? ORDER BY id",
            (stream_id,),
        )
        return [self._rebuild(t, d) for t, d in rows]

    def load_type(self, event_type: type) -> list:
        """Only the events of one type, in order (for example all TrackPlayed)."""
        rows = self._conn.execute("SELECT type, data FROM events WHERE type = ? ORDER BY id", (event_type.__name__,))
        return [self._rebuild(t, d) for t, d in rows]

    def load_all(self) -> list:
        rows = self._conn.execute("SELECT type, data FROM events ORDER BY id")
        return [self._rebuild(t, d) for t, d in rows]

    def _rebuild(self, type_name: str, data_json: str):
        return _from_json(self._types[type_name], json.loads(data_json))