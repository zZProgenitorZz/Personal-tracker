"""htmx routes for Fitness (#fitness): steps and gym visits.

Read only: everything comes in from the iPhone (POST /fitness/ingest/...), not through a form.
"""
import datetime as dt
from collections import defaultdict

from fastapi import APIRouter, Request

from .. import clock
from ..charts import day_of_month, last_days, last_weeks, with_height
from ..web import render
from .projections import GymProjection, StepsProjection, Visit, monday_of, week_totals

TABS = [("dashboard", "Dashboard"), ("history", "History")]
STEPS_DAYS = 14      # days in the steps chart
VISIT_WEEKS = 8      # weeks in the visits chart
HISTORY_DAYS = 60    # days in the History list


def duration_text(minutes: int | None) -> str:
    """45 -> '45 min', 65 -> '1 h 05 min', None -> 'unknown'"""
    if minutes is None:
        return "unknown"
    hours, rest = divmod(minutes, 60)
    return f"{hours} h {rest:02d} min" if hours else f"{minutes} min"


def fitness_summary(steps: StepsProjection, gym: GymProjection) -> dict:
    """For the home screen and Settings. "This week" = Monday up to now, as with Planner."""
    today, now = clock.today(), clock.local_now()
    week = week_totals(steps, gym, monday_of(today), now)
    visits = gym.visits(now)
    if visits and visits[-1].in_progress:
        note = f"At the gym since {visits[-1].start:%H:%M}"
    elif visits:
        note = f"Last visit: {visits[-1].start:%a} {visits[-1].start.day} {visits[-1].start:%b}"
    else:
        note = "Waiting for the first gym visit from your iPhone."
    return {
        "stats": [(week.visits, "gym visits"), (week.gym_hours, "gym hours"), (week.average_steps, "avg steps")],
        "current": [],
        "note": note,
        "count": len(visits),
        "unit": "gym visits",
    }


def history_rows(steps: StepsProjection, gym: GymProjection, today: dt.date, now: dt.datetime) -> list[dict]:
    """One row per day that has steps or visits, newest first."""
    start = today - dt.timedelta(days=HISTORY_DAYS - 1)
    per_day = steps.between(start, today)
    visits: dict[dt.date, list[Visit]] = defaultdict(list)
    for visit in gym.between(start, today, now):
        visits[visit.day].append(visit)
    return [{"day": day, "steps": per_day.get(day), "visits": visits.get(day, [])}
            for day in sorted({*per_day, *visits}, reverse=True)]


def create_fitness_web_router(steps: StepsProjection, gym: GymProjection) -> APIRouter:
    router = APIRouter(prefix="/ui/fitness", include_in_schema=False)

    @router.get("/dashboard")
    def dashboard(request: Request):
        today, now = clock.today(), clock.local_now()
        visits = gym.visits(now)
        return render(
            request, "fitness_dashboard.html", tabs=TABS, summary=fitness_summary(steps, gym),
            week=week_totals(steps, gym, monday_of(today), now), streak=gym.streak_weeks(today, now),
            steps_bars=with_height(last_days(steps.per_day(), today, STEPS_DAYS, label=day_of_month)),
            visit_weeks=with_height(last_weeks(gym.per_day(now), today, VISIT_WEEKS)),
            recent=list(reversed(visits[-5:])), has_data=bool(visits or steps.per_day()),
            duration=duration_text,
        )

    @router.get("/history")
    def history(request: Request):
        return render(request, "fitness_history.html", tabs=TABS, rows=history_rows(steps, gym, clock.today(),
                                                                                      clock.local_now()),
                      days=HISTORY_DAYS, duration=duration_text)

    return router
