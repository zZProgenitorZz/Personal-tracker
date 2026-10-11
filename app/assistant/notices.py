"""What needs Progen's attention today: the notices on Sunny's button and at the top of the panel.

Computed from the existing projections, with fixed lines in Sunny's voice: no AI call, no tokens,
no events. notices() is a pure function; hiding one for the day ("Not today") is kept in Sunny's
session, see visible().
"""
import datetime as dt
from dataclasses import dataclass, field

from ..reading.events import Status
from ..reading.projections import STALE_READING_DAYS
from ..watching.events import WatchStatus
from ..watching.projections import STALE_WATCHING_DAYS

MAX_DUSTY = 3            # per tracker
MAX_BACKLOG = 2
OLD_BACKLOG_DAYS = 120
STREAK_MIN = 3
STREAK_WARNING = dt.time(18)   # "ends tonight" only in the evening
ALL_DAY_LATE = dt.time(12)     # an all-day plan counts as overdue from noon


@dataclass(frozen=True)
class Action:
    label: str
    method: str                  # "post" for an existing /ui/... route, "link" for a page in the app
    url: str
    vals: dict = field(default_factory=dict)  # form fields for the route, e.g. {"status": "on_hold"}
    confirm: str | None = None   # a name: ask first, with the same dialog as elsewhere (hx-confirm)


@dataclass(frozen=True)
class Notice:
    key: str                     # stable for the day, for "Not today"
    kind: str                    # plans_today / streak / dusty_reading / dusty_watching / old_backlog
    text: str
    actions: list[Action] = field(default_factory=list)


def notices(data, now: dt.datetime) -> list[Notice]:
    """Everything worth saying right now, in a fixed order. `now` is local time without a time zone."""
    return [*_plans_today(data, now), *_streak(data, now), *_dusty_reading(data, now),
            *_dusty_watching(data, now), *_old_backlog(data, now)]


def visible(data, now: dt.datetime) -> list[Notice]:
    """The notices that weren't put away with "Not today" today."""
    return [n for n in notices(data, now) if (n.key, now.date()) not in data.dismissed]


def _days_quiet(updated_at: dt.datetime, now: dt.datetime) -> int:
    """Calendar days in the local time zone (a timedelta across a DST change would be an hour short)."""
    return (now.date() - updated_at.astimezone().date()).days


def _plans_today(data, now: dt.datetime) -> list[Notice]:
    found = []
    for o in data.agenda.day(now.date()):
        if o.done or (o.time is None and now.time() < ALL_DAY_LATE) or (o.time and o.starts_at > now):
            continue
        text = (f"{o.title} at {o.time:%H:%M} came and went. Done?" if o.time
                else f"{o.title} is today and still not ticked off.")
        done = Action("Done", "post", f"/ui/planner/plans/{o.plan.plan_id}/done", {"on": o.on.isoformat()})
        found.append(Notice(f"plan:{o.plan.plan_id}:{o.on.isoformat()}", "plans_today", text, [done]))
    return found


def _streak(data, now: dt.datetime) -> list[Notice]:
    today = now.date()
    days = data.reading_activity.streak(today)
    if now.time() < STREAK_WARNING or days < STREAK_MIN or data.reading_activity.per_day().get(today):
        return []
    return [Notice(f"streak:{today.isoformat()}", "streak",
                   f"Your {days}-day streak ends tonight. One chapter would do.")]


def _status_actions(url: str, title: str, on_hold: str, dropped: str) -> list[Action]:
    return [Action("On hold", "post", url, {"status": on_hold}),
            Action("Dropped", "post", url, {"status": dropped}, confirm=title)]


def _dusty_reading(data, now: dt.datetime) -> list[Notice]:
    return [Notice(f"dusty:reading:{e.series_id}", "dusty_reading",
                   f"{e.title} has been gathering dust for {_days_quiet(e.updated_at, now)} days.",
                   _status_actions(f"/ui/reading/series/{e.series_id}/status", e.title,
                                   Status.ON_HOLD.value, Status.DROPPED.value))
            for e in data.library.stale(now.astimezone(), STALE_READING_DAYS)[:MAX_DUSTY]]


def _dusty_watching(data, now: dt.datetime) -> list[Notice]:
    return [Notice(f"dusty:watching:{e.show_id}", "dusty_watching",
                   f"{e.title} has been gathering dust for {_days_quiet(e.updated_at, now)} days.",
                   _status_actions(f"/ui/watching/shows/{e.show_id}/status", e.title,
                                   WatchStatus.ON_HOLD.value, WatchStatus.DROPPED.value))
            for e in data.watchlist.stale(now.astimezone(), STALE_WATCHING_DAYS)[:MAX_DUSTY]]


def _old_backlog(data, now: dt.datetime) -> list[Notice]:
    """Plan to Read/Watch untouched for months. Removing goes through the Library and its own
    confirmation, so the action is a link there, not a delete button."""
    limit = now.astimezone() - dt.timedelta(days=OLD_BACKLOG_DAYS)
    items = [(e.updated_at, e.series_id, e.title, "Plan to Read", "#reading/library")
             for e in data.library.by_status(Status.PLAN_TO_READ) if e.updated_at < limit]
    items += [(e.updated_at, e.show_id, e.title, "Plan to Watch", "#watching/library")
              for e in data.watchlist.by_status(WatchStatus.PLAN_TO_WATCH) if e.updated_at < limit]
    return [Notice(f"backlog:{item_id}", "old_backlog",
                   f"{title} has been on {where} for {_days_quiet(updated_at, now)} days. Still keen?",
                   [Action("Remove from backlog", "link", url)])
            for updated_at, item_id, title, where, url in sorted(items)[:MAX_BACKLOG]]
