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