from ..domain import DomainError
from .events import TrackPlayed


class Play:
    """Eén play: alles wat er op één moment (played_at) geluisterd is. Plays
    worden nooit gewijzigd of verwijderd, dus er zijn alleen beslissingen voor
    het vastleggen."""

    def __init__(self, events: list):
        self.recorded = False
        for event in events:
            self._apply(event)

    # ---- Toestand opbouwen uit events ----

    def _apply(self, event) -> None:
        if isinstance(event, TrackPlayed):
            self.recorded = True

    # ---- Beslissingen: regels checken, nieuwe events teruggeven ----

    def record(self, command) -> list:
        if not command.track_id or not command.track_id.strip():
            raise DomainError("Een play heeft een track_id nodig")
        if command.duration_ms < 0:
            raise DomainError("duration_ms kan niet negatief zijn")
        if command.ms_played is not None and command.ms_played < 0:
            raise DomainError("ms_played kan niet negatief zijn")
        if self.recorded:
            return []  # al bekend: de sync haalt bewust overlappende data op
        event = TrackPlayed(
            command.played_at, command.track_id.strip(), command.track, list(command.artists),
            command.album, command.album_id, command.duration_ms, command.ms_played, command.source,
        )
        self._apply(event)
        return [event]
