from ..domain import DomainError
from .events import TrackPlayed


class Play:
    """One play: everything listened to at one moment (played_at). Plays are
    never changed or removed, so there are only decisions for recording them."""

    def __init__(self, events: list):
        self.recorded = False
        for event in events:
            self._apply(event)

    # ---- Building the state from events ----

    def _apply(self, event) -> None:
        if isinstance(event, TrackPlayed):
            self.recorded = True

    # ---- Decisions: check the rules, return new events ----

    def record(self, command) -> list:
        if not command.track_id or not command.track_id.strip():
            raise DomainError("A play needs a track_id")
        if command.duration_ms < 0:
            raise DomainError("duration_ms can't be negative")
        if command.ms_played is not None and command.ms_played < 0:
            raise DomainError("ms_played can't be negative")
        if self.recorded:
            return []  # already known: the sync fetches overlapping data on purpose
        event = TrackPlayed(
            command.played_at, command.track_id.strip(), command.track, list(command.artists),
            command.album, command.album_id, command.duration_ms, command.ms_played, command.source,
        )
        self._apply(event)
        return [event]
