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
