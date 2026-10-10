from app.eventstore import EventStore
from app.reading.events import Kind, ProgressLogged, SeriesStarted, Status, StatusChanged

EVENT_TYPES = [SeriesStarted, ProgressLogged, StatusChanged]


def make_store():
    return EventStore(":memory:", EVENT_TYPES)


def test_events_come_back_in_order():
    store = make_store()
    started = SeriesStarted("1", "Solo Leveling", Kind.MANHWA, "asura", 1)
    progress = ProgressLogged("1", 57, 1)

    store.append("1", [started, progress])

    assert store.load_stream("1") == [started, progress]


def test_streams_are_separate():
    store = make_store()
    store.append("1", [SeriesStarted("1", "Solo Leveling", Kind.MANHWA, "asura", 1)])
    store.append("2", [SeriesStarted("2", "Lord of the Mysteries", Kind.NOVEL, "novelphoenix.com", 1)])

    assert len(store.load_stream("1")) == 1
    assert len(store.load_all()) == 2


def test_enums_and_dates_survive_storage():
    store = make_store()
    changed = StatusChanged("1", Status.READING, Status.ON_HOLD)

    store.append("1", [changed])
    loaded = store.load_stream("1")[0]

    assert loaded.to_status is Status.ON_HOLD
    assert loaded.at == changed.at

def test_copy_is_a_complete_database(tmp_path):
    store = make_store()
    store.append("1", [SeriesStarted("1", "Solo Leveling", Kind.MANHWA, "asura", 1), ProgressLogged("1", 5, 1)])

    store.copy_to(tmp_path / "copy.db")

    copy = EventStore(str(tmp_path / "copy.db"), EVENT_TYPES)
    assert copy.load_all() == store.load_all()


def test_restore_replaces_all_events(tmp_path):
    old = make_store()
    old.append("1", [SeriesStarted("1", "Solo Leveling", Kind.MANHWA, "asura", 1)])
    old.copy_to(tmp_path / "old.db")

    store = make_store()
    store.append("2", [SeriesStarted("2", "Shadow Slave", Kind.NOVEL, "x", 1), ProgressLogged("2", 9, 1)])
    store.restore_from(tmp_path / "old.db")

    assert [e.title for e in store.load_all()] == ["Solo Leveling"]
    assert store.count() == 1
    store.append("3", [SeriesStarted("3", "Na terugzetten", Kind.NOVEL, "x", 1)])
    assert store.count() == 2


# ---- Datums, tijden, optionele velden en geneste dataclasses (voor Planner) ----

import datetime as dt  # noqa: E402
from dataclasses import dataclass, field  # noqa: E402
from enum import Enum  # noqa: E402


class Every(str, Enum):
    WEEKLY = "weekly"


@dataclass(frozen=True)
class Rule:
    every: Every
    weekdays: tuple[int, ...] = ()
    until: dt.date | None = None


@dataclass(frozen=True)
class SomethingPlanned:
    plan_id: str
    day: dt.date | None
    time: dt.time | None
    rule: Rule | None
    minutes: int | None = None
    at: dt.datetime = field(default_factory=lambda: dt.datetime(2026, 10, 9, 12, tzinfo=dt.timezone.utc))


def test_dates_times_optionals_and_nested_dataclasses_survive_storage():
    store = EventStore(":memory:", [SomethingPlanned])
    full = SomethingPlanned("p1", dt.date(2026, 10, 12), dt.time(14, 30),
                            Rule(Every.WEEKLY, (0, 3), dt.date(2026, 12, 31)), 45)
    empty = SomethingPlanned("p2", None, None, None)
    store.append("p1", [full])
    store.append("p2", [empty])
    assert store.load_all() == [full, empty]
    [back] = store.load_stream("p1")
    assert type(back.rule.every) is Every and type(back.rule.weekdays) is tuple and type(back.day) is dt.date


# ---- Snel genoeg voor een grote import ----

def test_streams_are_indexed_also_in_an_existing_database(tmp_path):
    import sqlite3
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)  # een database van vóór de index
    old.execute("CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT, stream_id TEXT NOT NULL, "
                "type TEXT NOT NULL, data TEXT NOT NULL, at TEXT NOT NULL)")
    old.commit()
    old.close()
    store = EventStore(str(path), [SeriesStarted])
    plan = store._conn.execute("EXPLAIN QUERY PLAN SELECT type, data FROM events WHERE stream_id = ? ORDER BY id",
                               ("x",)).fetchall()
    assert "idx_events_stream" in str(plan)
    store.close()


def test_a_transaction_stores_many_streams_at_once_and_notifies_after_commit():
    store = EventStore(":memory:", [SeriesStarted])
    seen = []
    store.subscribe(seen.append)
    with store.transaction():
        store.append("1", [SeriesStarted("1", "A", Kind.MANHWA, "x", 1)])
        assert store.load_stream("1")  # binnen de transactie al te lezen (regels blijven werken)
        store.append("2", [SeriesStarted("2", "B", Kind.MANHWA, "x", 1)])
        assert seen == []              # pas na het opslaan
    assert store.count() == 2 and [e.series_id for e in seen] == ["1", "2"]


def test_a_failed_transaction_stores_nothing():
    import pytest
    store = EventStore(":memory:", [SeriesStarted])
    seen = []
    store.subscribe(seen.append)
    with pytest.raises(RuntimeError):
        with store.transaction():
            store.append("1", [SeriesStarted("1", "A", Kind.MANHWA, "x", 1)])
            raise RuntimeError("halverwege mis")
    assert store.count() == 0 and seen == []


def test_load_type_reads_only_events_of_one_kind():
    store = EventStore(":memory:", [SeriesStarted, StatusChanged])
    store.append("1", [SeriesStarted("1", "A", Kind.MANHWA, "x", 1),
                       StatusChanged("1", Status.READING, Status.COMPLETED)])
    assert [type(e) for e in store.load_type(SeriesStarted)] == [SeriesStarted]
