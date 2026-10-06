"""Back-ups maken, bewaren (de 10 nieuwste) en terugzetten. Alles in tmp_path."""
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app.backup import KEEP_BACKUPS, BackupError, Backups, default_backup_dir, main
from app.eventstore import EventStore
from app.main import EVENT_TYPES
from app.reading.events import Kind, ProgressLogged, SeriesStarted


class Clock:
    """Nepklok: elke aanroep een minuut later, zodat back-ups verschillende namen krijgen."""
    def __init__(self):
        self.now = datetime(2026, 10, 6, 21, 0, 0)

    def __call__(self):
        self.now += timedelta(minutes=1)
        return self.now


def started(series_id, title):
    return SeriesStarted(series_id, title, Kind.NOVEL, "x", 1)


@pytest.fixture
def setup(tmp_path):
    store = EventStore(":memory:", EVENT_TYPES)
    covers = tmp_path / "covers"
    covers.mkdir()
    rebuilt = []
    backups = Backups(store, covers, tmp_path / "backups", clock=Clock(), on_restored=lambda: rebuilt.append(1))
    return store, covers, backups, rebuilt


# ---- Maken ----

def test_backup_is_named_after_date_and_time(setup):
    store, covers, backups, _ = setup
    store.append("1", [started("1", "Shadow Slave")])
    (covers / "a.webp").write_bytes(b"cover")

    info = backups.create()

    assert info.name == "2026-10-06_21-01-00"
    assert (info.path / "tracker.db").exists()
    assert (info.path / "covers" / "a.webp").read_bytes() == b"cover"
    assert (info.events, info.covers, info.reason) == (1, 1, "manual")
    assert json.loads((info.path / "info.json").read_text())["events"] == 1


def test_backup_contains_exactly_the_events(setup, tmp_path):
    store, _, backups, _ = setup
    store.append("1", [started("1", "Shadow Slave"), ProgressLogged("1", 9, 1)])
    info = backups.create()
    copy = EventStore(str(info.path / "tracker.db"), EVENT_TYPES)
    assert copy.load_all() == store.load_all()


def test_only_the_newest_ten_are_kept(setup):
    _, _, backups, _ = setup
    names = [backups.create().name for _ in range(KEEP_BACKUPS + 3)]
    assert [b.name for b in backups.list()] == list(reversed(names[-KEEP_BACKUPS:]))


def test_list_shows_newest_first(setup):
    _, _, backups, _ = setup
    first, second = backups.create(), backups.create()
    assert [b.name for b in backups.list()] == [second.name, first.name]


def test_two_backups_in_the_same_second_both_survive(tmp_path):
    store = EventStore(":memory:", EVENT_TYPES)
    same_time = lambda: datetime(2026, 10, 6, 21, 0, 0)  # noqa: E731
    backups = Backups(store, tmp_path / "covers", tmp_path / "backups", clock=same_time)
    a, b = backups.create(), backups.create()
    assert a.name != b.name and len(backups.list()) == 2


def test_failed_backup_leaves_nothing_behind(setup, monkeypatch):
    store, _, backups, _ = setup
    monkeypatch.setattr(store, "copy_to", lambda path: Path(path).write_bytes(b"kapot"))
    with pytest.raises(BackupError):
        backups.create()
    assert backups.list() == []
    assert list(backups.directory.iterdir()) == []


def test_unrelated_folders_are_ignored(setup):
    _, _, backups, _ = setup
    backups.create()
    (backups.directory / "mijn foto's").mkdir()
    assert len(backups.list()) == 1


# ---- Terugzetten ----

def test_restore_brings_back_the_old_events(setup):
    store, _, backups, rebuilt = setup
    store.append("1", [started("1", "Shadow Slave")])
    old = backups.create()
    store.append("2", [started("2", "Later toegevoegd")])

    backups.restore(old.name)

    assert [e.title for e in store.load_all()] == ["Shadow Slave"]
    assert rebuilt == [1]  # de read models worden opnieuw opgebouwd


def test_restore_first_saves_the_current_state(setup):
    store, _, backups, _ = setup
    store.append("1", [started("1", "Shadow Slave")])
    old = backups.create()
    store.append("2", [started("2", "Later toegevoegd")])

    backups.restore(old.name)

    [safety, _] = backups.list()
    assert safety.reason == "before-restore" and safety.events == 2
    backups.restore(safety.name)  # de terugzetting zelf is ook terug te draaien
    assert store.count() == 2


def test_restore_brings_back_missing_covers(setup):
    _, covers, backups, _ = setup
    (covers / "a.webp").write_bytes(b"cover")
    old = backups.create()
    (covers / "a.webp").unlink()
    backups.restore(old.name)
    assert (covers / "a.webp").read_bytes() == b"cover"


def test_restoring_the_oldest_backup_keeps_it(setup):
    _, _, backups, _ = setup
    oldest = backups.create()
    for _ in range(KEEP_BACKUPS - 1):
        backups.create()
    backups.restore(oldest.name)  # maakt een elfde (before-restore) aan
    names = [b.name for b in backups.list()]
    assert oldest.name in names and len(names) == KEEP_BACKUPS


@pytest.mark.parametrize("name", ["bestaat-niet", "../covers", "2026-10-06_21-01-00/../../x"])
def test_restore_unknown_backup_is_refused(setup, name):
    _, _, backups, _ = setup
    with pytest.raises(BackupError):
        backups.restore(name)


def test_damaged_backup_is_refused_and_nothing_changes(setup):
    store, _, backups, rebuilt = setup
    store.append("1", [started("1", "Shadow Slave")])
    old = backups.create()
    (old.path / "tracker.db").write_bytes(b"geen database")

    with pytest.raises(BackupError):
        backups.restore(old.name)
    assert store.count() == 1 and rebuilt == []
    assert len(backups.list()) == 1  # ook geen before-restore aangemaakt


# ---- Waar staan de back-ups? ----

def test_backup_dir_from_setting(monkeypatch, tmp_path):
    monkeypatch.setenv("PROGEN_BACKUP_DIR", str(tmp_path / "eigen"))
    assert default_backup_dir() == tmp_path / "eigen"


def test_backup_dir_in_onedrive(monkeypatch, tmp_path):
    monkeypatch.delenv("PROGEN_BACKUP_DIR", raising=False)
    monkeypatch.setenv("OneDrive", str(tmp_path))
    assert default_backup_dir() == tmp_path / "Progen-backups"


def test_backup_dir_without_onedrive(monkeypatch):
    monkeypatch.delenv("PROGEN_BACKUP_DIR", raising=False)
    monkeypatch.delenv("OneDrive", raising=False)
    assert default_backup_dir().name == "backups" and default_backup_dir().parent.name == "data"


# ---- Los script: python -m app.backup ----

def test_command_line_makes_and_lists_backups(tmp_path, capsys):
    store = EventStore(str(tmp_path / "tracker.db"), EVENT_TYPES)
    store.append("1", [started("1", "Shadow Slave")])
    args = ["--db", str(tmp_path / "tracker.db"), "--covers", str(tmp_path / "covers"),
            "--to", str(tmp_path / "backups")]

    assert main(args) == 0
    assert main([*args, "--list"]) == 0
    output = capsys.readouterr().out
    assert "1 events" in output and len(list((tmp_path / "backups").iterdir())) == 1
