"""htmx-routes voor Planner (#planner): Week, Day en Someday, snel invoeren, de dialog van
een plan, .ics voor je iPhone en de herinneringen in Settings.

GET-routes lezen uit de projecties. POST-routes sturen commands en antwoorden met een
toast plus het event `planner-changed`, waarop de zichtbare onderdelen zichzelf verversen.
"""
import datetime as dt
import re

from fastapi import APIRouter, Form, Request
from fastapi.responses import Response

from ..domain import DomainError
from ..web import render, toast
from . import schedule
from .aggregate import ReminderSettings
from .commands import (
    AddPlan, ChangeReminderSettings, EditPlan, MarkPlanDone, PlannerCommandHandler, RemovePlan, ReopenPlan,
    ReschedulePlan, SkipPlan,
)
from .events import Frequency, Repeat
from .ics import plan_to_ics
from .parse import SHORT_DAYS, Parsed, describe, describe_repeat, nice_day, parse
from .projections import AgendaProjection, Occurrence

CHANGED = "planner-changed"
TABS = [("week", "Week"), ("upcoming", "Upcoming"), ("day", "Day"), ("someday", "Someday")]
REMINDER_CHOICES = [(15, "15 minutes before"), (30, "30 minutes before"), (60, "1 hour before"),
                    (180, "3 hours before"), (24 * 60, "1 day before"), (2 * 24 * 60, "2 days before"),
                    (7 * 24 * 60, "1 week before")]
HOME_PLANS = 3  # het startscherm toont hooguit zoveel plannen


def today() -> dt.date:
    return schedule.local_now().date()


def monday_of(day: dt.date) -> dt.date:
    return day - dt.timedelta(days=day.weekday())


def parse_day(value: str | None, default: dt.date | None) -> dt.date | None:
    try:
        return dt.date.fromisoformat(value) if value else default
    except ValueError:
        return default


def parse_time(value: str | None) -> dt.time | None:
    try:
        return dt.time.fromisoformat(value) if value else None
    except ValueError:
        return None


def when_text(o: Occurrence) -> str:
    """'14:00', '14:00–15:00' of 'All day'."""
    if o.time is None:
        return "All day"
    return f"{o.time:%H:%M}–{o.ends_at:%H:%M}" if o.ends_at else f"{o.time:%H:%M}"


def upcoming_group(day: dt.date, today: dt.date) -> str:
    """Today, Tomorrow, This week, Next week, en daarna per maand ("November", "January 2027")."""
    if day == today:
        return "Today"
    if day == today + dt.timedelta(days=1):
        return "Tomorrow"
    this_monday = monday_of(today)
    if day < this_monday + dt.timedelta(days=7):
        return "This week"
    if day < this_monday + dt.timedelta(days=14):
        return "Next week"
    return f"{day:%B}" if day.year == today.year else f"{day:%B %Y}"


def capital(text: str) -> str:
    return text[:1].upper() + text[1:]


def week_label(monday: dt.date) -> str:
    sunday = monday + dt.timedelta(days=6)
    if monday.month == sunday.month:
        return f"{monday.day} – {sunday.day} {sunday:%b}"
    return f"{monday.day} {monday:%b} – {sunday.day} {sunday:%b}"


def planner_summary(agenda: AgendaProjection) -> dict:
    """Voor het startscherm en Settings: vandaag, het eerstvolgende plan, en hooguit twee andere."""
    now = schedule.local_now()
    plans = agenda.day(now.date())
    open_ = [o for o in plans if not o.done]
    upcoming = [o for o in open_ if o.time and o.time > now.time()]
    next_ = upcoming[0] if upcoming else None
    rest = [o for o in open_ if o is not next_ and (o.time is None or o.time > now.time())][:HOME_PLANS - 1]
    if next_:
        note = f"Next: {next_.title} at {next_.time:%H:%M}"
    elif rest:
        note = f"Today: {rest[0].title}"
        rest = rest[1:]
    else:
        note = "Nothing else planned today." if plans else "Nothing planned today."
    week = sum(len(items) for _, items in agenda.week(monday_of(now.date())))
    return {
        "stats": [(len(plans), "plans today"), (sum(o.done for o in plans), "done today"), (week, "this week")],
        "current": [],
        "agenda": [(when_text(o) if o.time else "All day", o.title) for o in rest],
        "note": note,
        "count": len(agenda.all()),
        "unit": "plans",
    }


def _repeat_from_form(mode: str, weekdays: list[str], until: str, parsed: Repeat | None) -> Repeat | None:
    """'auto' = wat uit de tekst kwam; anders wat er in "More" is gekozen."""
    until_day = parse_day(until, None)
    if mode == "none":
        return None
    if mode in Frequency._value2member_map_:
        days = tuple(sorted({int(d) for d in weekdays if d.isdigit() and 0 <= int(d) <= 6}))
        return Repeat(Frequency(mode), days, until_day)
    if parsed is not None and until_day is not None:
        return Repeat(parsed.every, parsed.weekdays, until_day)
    return parsed


def _minutes(value: str) -> int | None:
    return int(value) if value.strip().isdigit() else None


def create_planner_web_router(handler: PlannerCommandHandler, agenda: AgendaProjection) -> APIRouter:
    router = APIRouter(prefix="/ui/planner", include_in_schema=False)

    def act(request: Request, command, done_message: str, unchanged_message: str | None = None):
        try:
            events = handler.handle(command)
        except DomainError as exc:
            return toast(request, str(exc), error=True)
        if not events and unchanged_message:
            return toast(request, unchanged_message)
        return toast(request, done_message, changed=CHANGED)

    def title_of(plan_id: str) -> str:
        plan = agenda.get(plan_id)
        return plan.title if plan else "Plan"

    # ---- Lezen ----

    @router.get("/week")
    def week(request: Request, start: str = ""):
        monday = monday_of(parse_day(start, today()))
        days = agenda.week(monday)
        return render(request, "planner_week.html", tabs=TABS, days=days, monday=monday, today=today(),
                      label=week_label(monday), total=sum(len(items) for _, items in days),
                      previous=monday - dt.timedelta(days=7), following=monday + dt.timedelta(days=7),
                      this_week=monday_of(today()), when_text=when_text, short_days=SHORT_DAYS)

    @router.get("/upcoming")
    def upcoming(request: Request):
        now_day = today()
        one_offs, repeating = agenda.upcoming(now_day)
        groups: list[tuple[str, list[Occurrence]]] = []
        for o in one_offs:
            label = upcoming_group(o.on, now_day)
            if not groups or groups[-1][0] != label:
                groups.append((label, []))
            groups[-1][1].append(o)
        repeats = [{"plan": plan, "on": on, "o": Occurrence(plan, on),
                    "text": capital(describe_repeat(plan.repeat, plan.day))
                    + (f" · {plan.time:%H:%M}" if plan.time else "")} for plan, on in repeating]
        return render(request, "planner_upcoming.html", tabs=TABS, groups=groups, repeats=repeats,
                      totals={"plans": len(one_offs), "repeating": len(repeating),
                              "someday": len(agenda.someday())},
                      nice_day=lambda d: nice_day(d, now_day))

    @router.get("/day")
    def day(request: Request, d: str = ""):
        shown = parse_day(d, today())
        items = agenda.day(shown)
        return render(request, "planner_day.html", tabs=TABS, day=shown, items=items, today=today(),
                      label=nice_day(shown, today()), previous=shown - dt.timedelta(days=1),
                      following=shown + dt.timedelta(days=1), when_text=when_text)

    @router.get("/someday")
    def someday(request: Request):
        return render(request, "planner_someday.html", tabs=TABS, plans=agenda.someday(), done=agenda.someday_done(),
                      nice_day=lambda d: nice_day(d, today()))

    @router.get("/preview")
    def preview(request: Request, text: str = ""):
        """Wat de app van de invoer maakt, onder het invoerveld. Slaat niets op."""
        if not text.strip():
            return Response("")
        return render(request, "_planner_preview.html", text=describe(parse(text, schedule.local_now()), today()))

    @router.get("/plans/{plan_id}")
    def plan_dialog(request: Request, plan_id: str, on: str = ""):
        plan = agenda.get(plan_id)
        if plan is None:
            return toast(request, "Onbekend plan", error=True)
        shown = parse_day(on, plan.day or today())
        occurrence = Occurrence(plan, shown)
        when = "Someday" if plan.day is None else nice_day(shown, today())
        if plan.day is not None and plan.time:
            when += f" · {when_text(occurrence)}"
        return render(request, "_plan_dialog.html", plan=plan, o=occurrence, on=shown, when=when,
                      repeat_text=describe_repeat(plan.repeat, plan.day) if plan.repeat else "",
                      short_days=SHORT_DAYS, frequencies=[f.value for f in Frequency])

    @router.get("/{plan_id}.ics")
    def ics(plan_id: str):
        plan = agenda.get(plan_id)
        if plan is None:
            return Response("Plan not found", status_code=404)
        body = plan_to_ics(plan, agenda.reminder_settings, dt.datetime.now(dt.timezone.utc))
        name = re.sub(r"[^\w\- ]", "", plan.title).strip()[:60] or "plan"
        return Response(body, media_type="text/calendar; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{name}.ics"'})

    # ---- Schrijven ----

    @router.post("/plans")
    def add(request: Request, text: str = Form(""), note: str = Form(""), duration_min: str = Form(""),
            repeat: str = Form("auto"), weekdays: list[str] = Form([]), until: str = Form("")):
        parsed = parse(text, schedule.local_now())
        chosen = _repeat_from_form(repeat, weekdays, until, parsed.repeat)
        day = parsed.day if parsed.day or chosen is None else today()  # een herhaling begint op zijn vroegst vandaag
        try:
            handler.handle(AddPlan(parsed.title, day, parsed.time, _minutes(duration_min) or parsed.duration_min,
                                   note, chosen))
        except DomainError as exc:
            return toast(request, str(exc), error=True)
        shown = Parsed(parsed.title, day, parsed.time, chosen, _minutes(duration_min) or parsed.duration_min)
        return toast(request, f"Added {describe(shown, today())}",
                     changed=CHANGED)

    @router.post("/plans/{plan_id}/done")
    def done(request: Request, plan_id: str, on: str = Form("")):
        return act(request, MarkPlanDone(plan_id, parse_day(on, today())), f"{title_of(plan_id)} · done")

    @router.post("/plans/{plan_id}/reopen")
    def reopen(request: Request, plan_id: str, on: str = Form("")):
        return act(request, ReopenPlan(plan_id, parse_day(on, today())), f"{title_of(plan_id)} · not done")

    @router.post("/plans/{plan_id}/skip")
    def skip(request: Request, plan_id: str, on: str = Form("")):
        on_day = parse_day(on, today())
        return act(request, SkipPlan(plan_id, on_day), f"{title_of(plan_id)} · skipped on {nice_day(on_day, today())}")

    @router.post("/plans/{plan_id}/reschedule")
    def reschedule(request: Request, plan_id: str, day: str = Form(""), time: str = Form("")):
        new_day, new_time = parse_day(day, None), parse_time(time)
        where = "Someday" if new_day is None else nice_day(new_day, today()) + (f" {new_time:%H:%M}" if new_time else "")
        return act(request, ReschedulePlan(plan_id, new_day, new_time), f"{title_of(plan_id)} → {where}",
                   unchanged_message=f"{title_of(plan_id)}: nothing changed")

    @router.post("/plans/{plan_id}/edit")
    def edit(request: Request, plan_id: str, title: str = Form(""), note: str = Form(""),
             duration_min: str = Form(""), repeat: str = Form("none"), weekdays: list[str] = Form([]),
             until: str = Form("")):
        chosen = _repeat_from_form(repeat, weekdays, until, None)
        return act(request, EditPlan(plan_id, title, note, _minutes(duration_min), chosen),
                   f"Saved {title.strip()}", unchanged_message="Nothing changed")

    @router.delete("/plans/{plan_id}")
    def remove(request: Request, plan_id: str):
        return act(request, RemovePlan(plan_id), f"Removed {title_of(plan_id)}")

    # ---- Herinneringen (in Settings) ----

    @router.get("/reminders")
    def reminders(request: Request):
        first_on, first_min, second_on, second_min, all_day_time = agenda.reminder_settings
        choices = sorted({*REMINDER_CHOICES, *[(m, f"{m} minutes before") for m in (first_min, second_min)
                                               if m not in dict(REMINDER_CHOICES)]})
        return render(request, "_planner_reminders.html", choices=choices, first_on=first_on, first_min=first_min,
                      second_on=second_on, second_min=second_min, all_day_time=all_day_time,
                      default=ReminderSettings.DEFAULT)

    @router.post("/reminders")
    def save_reminders(request: Request, first_enabled: str = Form(""), first_minutes: int = Form(24 * 60),
                       second_enabled: str = Form(""), second_minutes: int = Form(180),
                       all_day_time: str = Form("09:00")):
        command = ChangeReminderSettings(bool(first_enabled), first_minutes, bool(second_enabled), second_minutes,
                                         parse_time(all_day_time) or dt.time(9))
        return act(request, command, "Planner reminders saved", unchanged_message="Reminders unchanged")

    return router
