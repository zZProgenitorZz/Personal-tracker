"""The app's clock, in one place. Tests set the time by patching app.clock."""
import datetime as dt


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def local_now() -> dt.datetime:
    """This PC's clock without a time zone: plans are in local time."""
    return dt.datetime.now()


def today() -> dt.date:
    """The local date."""
    return local_now().date()


def to_local(moment: dt.datetime) -> dt.datetime:
    """A moment with a UTC offset (an iPhone's "2026-10-16T18:30:00+02:00") as this PC's local time
    without a time zone. A moment without an offset is taken to be local time already."""
    return moment.astimezone().replace(tzinfo=None) if moment.tzinfo else moment
