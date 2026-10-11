"""Sunny's tools (see identity/TOOLS.md): JSON schemas with strict: true, and what they do.

Read tools call the existing projections directly (no HTTP) and return compact JSON:
only the fields that are needed. Write tools don't run anything: they return a PendingAction
(the command plus a readable line for the card). Only after Confirm does the command go to
the ordinary command handler and become an ordinary event.

An invalid call (unknown id, wrong date) becomes a ToolError: it goes back to the model as
is_error, so Sunny can fix it itself or ask.
"""
import datetime as dt
from dataclasses import dataclass, field
from typing import Any, Callable

from rapidfuzz import fuzz, process

from ..planner.commands import AddPlan, EditPlan, MarkPlanDone, PlannerCommandHandler, RemovePlan, ReschedulePlan, SkipPlan
from ..planner.parse import describe, describe_repeat, nice_day, parse
from ..planner.projections import AgendaProjection
from ..planner.schedule import next_occurrence
from ..reading.commands import ChangeStatus, LogProgress, ReadingCommandHandler, RemoveSeries, StartSeries
from ..reading.events import KIND_LABELS, STATUS_LABELS, Kind, Status
from ..reading.projections import LibraryProjection, ReadingActivityProjection, ReadingHistoryProjection
from ..listening.projections import ListeningActivityProjection, TopArtistsProjection, TopTracksProjection
from .notices import visible
from ..watching.commands import AddShow, ChangeShowStatus, RemoveShow, WatchingCommandHandler
from ..watching.events import WATCH_KIND_LABELS, WATCH_STATUS_LABELS, WatchKind, WatchStatus
from ..watching.projections import WatchHistoryProjection, WatchlistProjection

PROPOSED = "proposed, waiting for Progen's confirmation"
MAX_ROWS = 40  # never the whole library in one answer
MAX_PLANS = 100  # plans_between: at most this many occurrences (repeats expanded)


class ToolError(ValueError):
    """A call that can't be done; the message is for the model."""


@dataclass
class PendingAction:
    domain: str                 # reading / watching / planner
    command: Any                # the command for the handler, after Confirm
    label: str                  # "Solo Leveling · chapter 119 → 120"
    changed: str                # htmx event after Confirm, e.g. "reading-changed"
    delete_name: str | None = None  # when removing: the name, for the extra confirmation


@dataclass
class TrackerData:
    """Everything Sunny may access: the projections to read, the handlers to write (after Confirm)."""
    library: LibraryProjection
    watchlist: WatchlistProjection
    listening: ListeningActivityProjection
    top_artists: TopArtistsProjection
    top_tracks: TopTracksProjection
    agenda: AgendaProjection
    reading_activity: ReadingActivityProjection
    reading_history: ReadingHistoryProjection
    watch_history: WatchHistoryProjection
    reading: ReadingCommandHandler
    watching: WatchingCommandHandler
    planner: PlannerCommandHandler
    now: Callable[[], dt.datetime]   # local time, like the planner
    # Notices put away with "Not today": (key, local date). Sunny's session shares this set.
    dismissed: set = field(default_factory=set)

    def handler(self, domain: str):
        return {"reading": self.reading, "watching": self.watching, "planner": self.planner}[domain]


# ---- The schemas ----

def _nullable(schema: dict) -> dict:
    return {"anyOf": [schema, {"type": "null"}]}


MAX_STRICT_TOOLS = 20  # the API refuses a request with more strict tools (400)


def _tool(name: str, description: str, properties: dict, strict: bool = False) -> dict:
    """Read tools aren't strict: bad input becomes a ToolError in run_tool, and the model may leave out
    a nullable field. Write tools are (see _write): what they propose must have exactly the right shape."""
    return {"name": name, "description": description, "strict": strict,
            "input_schema": {"type": "object", "properties": properties, "required": list(properties),
                             "additionalProperties": False}}


def _write(name: str, description: str, properties: dict) -> dict:
    return _tool(name, description + WRITE_NOTE, properties, strict=True)


def _enum(*values) -> dict:
    return {"type": "string", "enum": list(dict.fromkeys(values))}


DATE = {"type": "string", "description": "YYYY-MM-DD"}
DOMAIN = _enum("reading", "watching")
ANY_STATUS = _enum(*(s.value for s in Status), *(s.value for s in WatchStatus))  # checked against the domain
ANY_KIND = _enum(*(k.value for k in Kind), *(k.value for k in WatchKind))         # the kind decides the domain
LIMIT = {"type": "integer", "description": "How many, e.g. 5"}
WRITE_NOTE = " Only proposes the change; nothing is saved until Progen clicks Confirm."

TOOL_DEFINITIONS = [
    # ---- Read (not strict) ----
    _tool("find_item", "Find a series, show or plan by (part of) its title, tolerant of typos. Use this first to get "
                       "an id instead of listing everything.",
          {"query": {"type": "string"},
           "domain": _nullable({"type": "string", "enum": ["reading", "watching", "plan"]})}),
    _tool("library", "The reading library or the watch list: id, title, status (and chapter or kind). "
                     "Optionally only one status. For past activity use history_between.",
          {"domain": DOMAIN, "status": _nullable({"type": "string"})}),
    _tool("listening_stats", "Listening between two dates (inclusive): minutes, days listened, top artists and "
                             "top tracks.", {"from": DATE, "to": DATE, "limit": _nullable(LIMIT)}),
    _tool("plans_between", "Every planned time between two dates (inclusive), repeats worked out.",
          {"from": DATE, "to": DATE}),
    _tool("history_between", "What was read and watched between two dates (inclusive): chapters, status changes, "
                             "finished shows, totals. Shows added as already completed have no finish date. "
                             "Watching has no episodes, only status periods.",
          {"from": DATE, "to": DATE, "domain": _nullable(DOMAIN)}),
    _tool("notices", "What needs Progen's attention today.", {}),
    _tool("item_history", "The full history of one series or show (removed earlier versions included): added, "
                          "status changes with dates, times completed, reading days and chapters.",
          {"domain": DOMAIN, "id": {"type": "string"}}),
    # ---- Write (strict) ----
    _write("set_reading_progress", "Set the chapter Progen is at for a series.",
           {"series_id": {"type": "string"}, "chapter": {"type": "number"}}),
    _write("set_status", "Change the status of a series (reading) or a movie, series or anime (watching; "
                         "rewatching for a second watch).", {"domain": DOMAIN, "id": {"type": "string"},
                                                             "status": ANY_STATUS}),
    _write("add_item", "Add a novel or manhwa to the reading library, or a movie, series or anime to the watch "
                       "list. Status null = reading / watching.",
           {"title": {"type": "string"}, "kind": ANY_KIND, "status": _nullable(ANY_STATUS)}),
    _write("add_plan", "Add a plan from plain language, Dutch or English, e.g. \"elke ma 12:00 gym\" or "
                       "\"tandarts vr 14u\". The planner's own parser reads date, time and repeat.",
           {"text": {"type": "string"}}),
    _write("plan_done", "Tick off a plan (for a repeat: one time; default the next one).",
           {"plan_id": {"type": "string"}, "on": _nullable(DATE)}),
    _write("plan_skip", "Skip one time of a repeating plan.", {"plan_id": {"type": "string"}, "on": DATE}),
    _write("plan_reschedule", "Move a plan to another date and/or time (day null = Someday, time null = all day).",
           {"plan_id": {"type": "string"}, "day": _nullable(DATE),
            "time": _nullable({"type": "string", "description": "HH:MM"})}),
    _write("plan_edit", "Change a plan's title and/or note (null keeps it).",
           {"plan_id": {"type": "string"}, "title": _nullable({"type": "string"}),
            "note": _nullable({"type": "string"})}),
    _write("delete_item", "Delete a series, show or plan. Progen gets an extra confirmation with the name.",
           {"domain": {"type": "string", "enum": ["reading", "watching", "plan"]}, "id": {"type": "string"}}),
]
TOOL_DEFINITIONS[-1]["cache_control"] = {"type": "ephemeral"}  # caches the whole tool list


# ---- Running ----

def _date(value: str | None, name: str) -> dt.date:
    try:
        return dt.date.fromisoformat(value)
    except (TypeError, ValueError):
        raise ToolError(f"'{name}' must be a date like 2026-10-14") from None


def _period(args: dict) -> tuple[dt.date, dt.date]:
    start, end = _date(args["from"], "from"), _date(args["to"], "to")
    if end < start:
        raise ToolError("'from' must not be after 'to'")
    return start, end


def _limit(args: dict) -> int:
    return max(1, min(int(args.get("limit") or 5), 25))


def _lookup(found, noun: str, item_id: str):
    """The item found, or a ToolError that sends the model to find_item."""
    if found is None:
        raise ToolError(f"No {noun} with id {item_id!r}. Use find_item to get the id.")
    return found


def _series(data: TrackerData, series_id: str):
    return _lookup(data.library.get(series_id), "series", series_id)


def _show(data: TrackerData, show_id: str):
    return _lookup(data.watchlist.get(show_id), "show", show_id)


def _plan(data: TrackerData, plan_id: str):
    return _lookup(data.agenda.get(plan_id), "plan", plan_id)


def _when(day: dt.date | None, time: dt.time | None, today: dt.date) -> str:
    if day is None:
        return "Someday"
    return nice_day(day, today) + (f" {time:%H:%M}" if time else "")


def _plan_row(plan, today: dt.date) -> dict:
    upcoming = next_occurrence(plan, today)
    return {"domain": "plan", "id": plan.plan_id, "title": plan.title,
            "next": upcoming.isoformat() if upcoming else None,
            "time": plan.time.strftime("%H:%M") if plan.time else None,
            "repeat": describe_repeat(plan.repeat, plan.day) if plan.repeat else None}


def find_item(args: dict, data: TrackerData) -> list[dict]:
    today = data.now().date()
    domain = args.get("domain")
    rows: list[tuple[str, dict]] = []
    if domain in (None, "reading"):
        rows += [(e.title, {"domain": "reading", "id": e.series_id, "title": e.title, "status": e.status.value,
                            "chapter": e.current_chapter}) for e in data.library.all()]
    if domain in (None, "watching"):
        rows += [(e.title, {"domain": "watching", "id": e.show_id, "title": e.title, "kind": e.kind.value,
                            "status": e.status.value}) for e in data.watchlist.all()]
    if domain in (None, "plan"):
        rows += [(p.title, _plan_row(p, today)) for p in data.agenda.all()]
    matches = process.extract(args["query"], [title for title, _ in rows], scorer=fuzz.WRatio,
                              processor=str.lower, limit=5, score_cutoff=60)
    return [rows[index][1] for _, _, index in matches]


def _status(domain: str, value: str | None):
    """The status as the domain's enum, or a ToolError that names the right ones (the schema allows both)."""
    if not value:
        return None
    statuses = Status if domain == "reading" else WatchStatus
    try:
        return statuses(value)
    except ValueError:
        raise ToolError(f"'{value}' is not a {domain} status. Use one of: "
                        + ", ".join(s.value for s in statuses)) from None


def _domain(args: dict) -> str:
    domain = args.get("domain")
    if domain not in ("reading", "watching"):
        raise ToolError("'domain' must be reading or watching")
    return domain


def library(args: dict, data: TrackerData) -> list[dict]:
    domain = _domain(args)
    status = _status(domain, args.get("status"))
    if domain == "reading":
        today = data.now().date()
        entries = data.library.by_status(status) if status else data.library.all()
        entries = sorted(entries, key=lambda e: e.updated_at, reverse=True)[:MAX_ROWS]
        return [{"id": e.series_id, "title": e.title, "status": e.status.value, "chapter": e.current_chapter,
                 "last_update": e.updated_at.astimezone().date().isoformat(),
                 "days_ago": (today - e.updated_at.astimezone().date()).days} for e in entries]
    entries = data.watchlist.by_status(status) if status else data.watchlist.all()
    return [{"id": e.show_id, "title": e.title, "kind": e.kind.value, "status": e.status.value}
            for e in entries[:MAX_ROWS]]


def listening_stats(args: dict, data: TrackerData) -> dict:
    start, end = _period(args)
    limit = _limit(args)
    days = {d: m for d, m in data.listening.minutes_per_day().items() if start <= d <= end}
    return {"minutes": round(sum(days.values())), "days_listened": sum(1 for m in days.values() if m > 0),
            "top_artists": [{"artist": a, "plays": n} for a, n in data.top_artists.between(start, end, limit=limit)],
            "top_tracks": [{"track": t.track, "artists": t.artists, "plays": t.plays, "minutes": round(t.minutes, 1)}
                           for t in data.top_tracks.between(start, end, limit=limit)]}


def plans_between(args: dict, data: TrackerData) -> list[dict]:
    return [{"plan_id": o.plan.plan_id, "title": o.title, "on": o.on.isoformat(),
             "time": o.time.strftime("%H:%M") if o.time else None, "done": o.done, "repeats": o.is_repeat}
            for o in data.agenda.between(*_period(args))][:MAX_PLANS]


def _num(value: float | None):
    """57.0 -> 57, 45.5 -> 45.5: compact JSON."""
    return int(value) if value is not None and float(value).is_integer() else value


def _change(change) -> str:
    """(day, from, to) -> "completed 2026-09-14" """
    return f"{change[2].value} {change[0].isoformat()}"


def history_between(args: dict, data: TrackerData) -> dict:
    start, end = _period(args)
    domain = args.get("domain")
    result, totals = {}, {"finished": 0}
    if domain in (None, "reading"):
        period = data.reading_history.between(start, end)
        result["reading"] = [{"title": r.title, "kind": r.kind.value, "chapters": _num(r.chapters),
                              "from_chapter": _num(r.from_chapter), "to_chapter": _num(r.to_chapter),
                              "changes": [_change(c) for c in r.changes]} for r in period.rows[:MAX_ROWS]]
        totals.update(chapters=_num(period.chapters), days_read=period.days_read)
        totals["finished"] += period.completed
    if domain in (None, "watching"):
        rows = data.watch_history.between(start, end)
        result["watching"] = [{"title": r.title, "kind": r.kind.value,
                               "finished_on": r.finished_on.isoformat() if r.finished_on else None,
                               "rewatch": r.rewatch} for r in rows[:MAX_ROWS]]
        totals["finished"] += sum(r.finished_on is not None for r in rows)
    return {**result, "totals": totals}


def notices(args: dict, data: TrackerData) -> list[dict]:
    return [{"kind": n.kind, "text": n.text} for n in visible(data, data.now())]


def item_history(args: dict, data: TrackerData) -> dict:
    domain, item_id = args["domain"], args["id"]
    if domain == "reading":
        history = data.reading_history
        t = history.timeline(_lookup(history.key_of(item_id), "series", item_id))
        return {"title": t.title, "kind": t.kind.value, "added": t.added.isoformat(),
                "start_status": t.start_status.value, "changes": [_change(c) for c in t.changes],
                "times_completed": t.times_completed, "chapters": _num(t.chapters),
                "first_read": t.first_read.isoformat() if t.first_read else None,
                "last_read": t.last_read.isoformat() if t.last_read else None}
    if domain == "watching":
        history = data.watch_history
        t = history.timeline(_lookup(history.key_of(item_id), "show", item_id))
        return {"title": t.title, "kind": t.kind.value, "added": t.added.isoformat(),
                "start_status": t.start_status.value, "changes": [_change(c) for c in t.changes],
                "times_completed": t.times_completed,
                "completed_on": [d.isoformat() if d else "date unknown" for d in t.completed],
                "times_rewatched": t.times_rewatched}
    raise ToolError("'domain' must be reading or watching")


# ---- Write tools: proposals only ----

def set_reading_progress(args: dict, data: TrackerData) -> PendingAction:
    entry = _series(data, args["series_id"])
    chapter = float(args["chapter"])
    if chapter < 0:
        raise ToolError("A chapter can't be negative")
    if chapter == entry.current_chapter:
        raise ToolError(f"{entry.title} is already at chapter {chapter:g}; nothing to change")
    return PendingAction("reading", LogProgress(entry.series_id, chapter),
                         f"{entry.title} · chapter {entry.current_chapter:g} → {chapter:g}", "reading-changed")


def set_status(args: dict, data: TrackerData) -> PendingAction:
    domain = _domain(args)
    status = _status(domain, args.get("status"))
    if status is None:
        raise ToolError("'status' is required")
    if domain == "reading":
        entry = _series(data, args["id"])
        if status is entry.status:
            raise ToolError(f"{entry.title} is already {status.value}")
        return PendingAction("reading", ChangeStatus(entry.series_id, status),
                             f"{entry.title} · {STATUS_LABELS[entry.status]} → {STATUS_LABELS[status]}",
                             "reading-changed")
    entry = _show(data, args["id"])
    if status is entry.status:
        raise ToolError(f"{entry.title} is already {status.value}")
    return PendingAction("watching", ChangeShowStatus(entry.show_id, status),
                         f"{entry.title} · {WATCH_STATUS_LABELS[entry.status]} → {WATCH_STATUS_LABELS[status]}",
                         "watching-changed")


def add_item(args: dict, data: TrackerData) -> PendingAction:
    """The kind decides the domain: novel or manhwa → reading, movie, series or anime → watching."""
    title, kind = args["title"].strip(), args["kind"]
    if not title:
        raise ToolError("A title is needed")
    if kind in (k.value for k in Kind):
        kind, status = Kind(kind), _status("reading", args.get("status")) or Status.READING
        if existing := data.library.find_by_title(title):
            raise ToolError(f"'{existing.title}' is already in the library (id {existing.series_id})")
        label = f"New {KIND_LABELS[kind].lower()} · {title}"
        if status is not Status.READING:
            label += f" ({STATUS_LABELS[status]})"
        return PendingAction("reading", StartSeries(title, kind, "", status=status), label, "reading-changed")
    kind = WatchKind(kind)
    status = _status("watching", args.get("status")) or WatchStatus.WATCHING
    if existing := data.watchlist.find(title, kind):
        raise ToolError(f"'{existing.title}' is already in the list (id {existing.show_id})")
    return PendingAction("watching", AddShow(title, kind, status),
                         f"New {WATCH_KIND_LABELS[kind].lower()} · {title} ({WATCH_STATUS_LABELS[status]})",
                         "watching-changed")


def add_plan(args: dict, data: TrackerData) -> PendingAction:
    now = data.now()
    parsed = parse(args["text"], now)
    if not parsed.title:
        raise ToolError("The plan needs a title besides the date and time")
    command = AddPlan(parsed.title, parsed.day, parsed.time, parsed.duration_min, "", parsed.repeat)
    return PendingAction("planner", command, f"New plan · {describe(parsed, now.date())}", "planner-changed")


def plan_done(args: dict, data: TrackerData) -> PendingAction:
    plan, today = _plan(data, args["plan_id"]), data.now().date()
    on = _date(args["on"], "on") if args.get("on") else (next_occurrence(plan, today) or today)
    return PendingAction("planner", MarkPlanDone(plan.plan_id, on), f"{plan.title} · done ({nice_day(on, today)})",
                         "planner-changed")


def plan_skip(args: dict, data: TrackerData) -> PendingAction:
    plan, today = _plan(data, args["plan_id"]), data.now().date()
    if plan.repeat is None:
        raise ToolError(f"{plan.title} doesn't repeat; reschedule or delete it instead")
    on = _date(args["on"], "on")
    return PendingAction("planner", SkipPlan(plan.plan_id, on), f"{plan.title} · skip {nice_day(on, today)}",
                         "planner-changed")


def plan_reschedule(args: dict, data: TrackerData) -> PendingAction:
    plan, today = _plan(data, args["plan_id"]), data.now().date()
    day = _date(args["day"], "day") if args.get("day") else None
    try:
        time = dt.time.fromisoformat(args["time"]) if args.get("time") else None
    except ValueError:
        raise ToolError("'time' must look like 14:30") from None
    return PendingAction("planner", ReschedulePlan(plan.plan_id, day, time),
                         f"{plan.title} · {_when(plan.day, plan.time, today)} → {_when(day, time, today)}",
                         "planner-changed")


def plan_edit(args: dict, data: TrackerData) -> PendingAction:
    plan = _plan(data, args["plan_id"])
    title = args["title"] if args.get("title") is not None else plan.title
    note = args["note"] if args.get("note") is not None else plan.note
    changes = ([f"{plan.title} → {title}"] if title != plan.title else []) + (["new note"] if note != plan.note else [])
    if not changes:
        raise ToolError("Nothing to change")
    return PendingAction("planner", EditPlan(plan.plan_id, title, note, plan.duration_min, plan.repeat),
                         f"{plan.title} · " + ", ".join(changes), "planner-changed")


def delete_item(args: dict, data: TrackerData) -> PendingAction:
    domain, item_id = args["domain"], args["id"]
    if domain == "reading":
        entry = _series(data, item_id)
        name, command, target, changed = entry.title, RemoveSeries(item_id), "reading", "reading-changed"
    elif domain == "watching":
        entry = _show(data, item_id)
        name, command, target, changed = entry.title, RemoveShow(item_id), "watching", "watching-changed"
    else:
        plan = _plan(data, item_id)
        name, command, target, changed = plan.title, RemovePlan(item_id), "planner", "planner-changed"
    return PendingAction(target, command, f"Delete · {name}", changed, delete_name=name)


READ_TOOLS = {f.__name__: f for f in (find_item, library, listening_stats, plans_between, history_between, notices,
                                      item_history)}
WRITE_TOOLS = {f.__name__: f for f in (set_reading_progress, set_status, add_item, add_plan, plan_done, plan_skip,
                                       plan_reschedule, plan_edit, delete_item)}

def run_tool(name: str, args: dict, data: TrackerData) -> tuple[Any, PendingAction | None]:
    """(result, None) for a read tool; (PROPOSED, PendingAction) for a write tool."""
    try:
        if name in READ_TOOLS:
            return READ_TOOLS[name](args, data), None
        if name in WRITE_TOOLS:
            return PROPOSED, WRITE_TOOLS[name](args, data)
    except (KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, ToolError):
            raise
        raise ToolError(f"Invalid input for {name}: {exc}") from None
    raise ToolError(f"Unknown tool {name}")
