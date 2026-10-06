import json
import sqlite3
import threading
from dataclasses import asdict, fields
from datetime import datetime
from enum import Enum
from pathlib import Path


def _to_json(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    raise TypeError(f"Kan {type(value)} niet opslaan")


class EventStore:
    def __init__(self, path: str, event_types: list[type]):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._subscribers = []
        self._types = {t.__name__: t for t in event_types}
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.RLock()  # schrijven, kopiëren en terugzetten nooit door elkaar
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS events (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                stream_id TEXT NOT NULL,
                type      TEXT NOT NULL,
                data      TEXT NOT NULL,
                at        TEXT NOT NULL
            )"""
        )
        self._conn.commit()

    def subscribe(self, callback) -> None:
        self._subscribers.append(callback)

    def append(self, stream_id: str, events: list) -> None:
        with self._lock, self._conn:
            for event in events:
                self._conn.execute(
                    "INSERT INTO events (stream_id, type, data, at) VALUES (?, ?, ?, ?)",
                    (
                        stream_id,
                        type(event).__name__,
                        json.dumps(asdict(event), default=_to_json),
                        event.at.isoformat(),
                    ),
                )
        for event in events:
            for callback in self._subscribers:
                callback(event)

    def count(self) -> int:
        with self._lock:
            return self._conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]

    # ---- Back-up ----
    # Via de back-up-API van SQLite: altijd een complete, kloppende kopie,
    # ook als er op hetzelfde moment iets wordt weggeschreven.

    def copy_to(self, path) -> None:
        target = sqlite3.connect(str(path))
        try:
            with self._lock:
                self._conn.backup(target)
        finally:
            target.close()

    def restore_from(self, path) -> None:
        """Vervang alle events door die uit de kopie op `path`."""
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

    def load_all(self) -> list:
        rows = self._conn.execute("SELECT type, data FROM events ORDER BY id")
        return [self._rebuild(t, d) for t, d in rows]

    def _rebuild(self, type_name: str, data_json: str):
        cls = self._types[type_name]
        data = json.loads(data_json)
        for f in fields(cls):
            if f.type is datetime:
                data[f.name] = datetime.fromisoformat(data[f.name])
            elif isinstance(f.type, type) and issubclass(f.type, Enum):
                data[f.name] = f.type(data[f.name])
        return cls(**data)