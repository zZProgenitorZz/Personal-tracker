"""JSON-API van Planner: voor scripts, en /planner/due voor de meldingen van het icoon bij de klok."""
import datetime as dt

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from . import schedule
from .commands import AddPlan, PlannerCommandHandler
from .parse import parse
from .projections import AgendaProjection, PlanEntry


class AddPlanBody(BaseModel):
    text: str = Field(min_length=1, description='In gewone taal, bv. "Tandarts vr 14u" of "elke ma gym 7:00"')
    note: str = ""
    duration_min: int | None = None


def plan_json(plan: PlanEntry) -> dict:
    repeat = plan.repeat
    return {
        "plan_id": plan.plan_id, "title": plan.title, "day": plan.day, "time": plan.time,
        "duration_min": plan.duration_min, "note": plan.note,
        "repeat": None if repeat is None else {"every": repeat.every.value, "weekdays": list(repeat.weekdays),
                                               "until": repeat.until},
        "done": sorted(plan.done), "skipped": sorted(plan.skipped),
    }


def create_planner_router(handler: PlannerCommandHandler, agenda: AgendaProjection) -> APIRouter:
    router = APIRouter(prefix="/planner", tags=["planner"])

    @router.get("/plans")
    def plans():
        return [plan_json(p) for p in agenda.all()]

    @router.get("/day")
    def day(d: dt.date | None = None):
        day = d or schedule.local_now().date()
        return [{"on": o.on, "done": o.done, **plan_json(o.plan)} for o in agenda.day(day)]

    @router.get("/due")
    def due(minutes: int = Query(1, ge=1, le=60), since: dt.datetime | None = None):
        """Herinneringen die in de afgelopen `minutes` minuten vielen, of sinds `since` (lokale tijd,
        hooguit een uur terug). Alleen lezen: geen command, geen event. Het icoon bij de klok vraagt
        dit elke minuut op, steeds sinds zijn vorige vraag, en meldt elke `key` maar één keer."""
        now = schedule.local_now()
        window = dt.timedelta(minutes=minutes)
        if since is not None:
            window = min(now - since.replace(tzinfo=None), dt.timedelta(hours=1))
            if window <= dt.timedelta(0):
                return []
        return [{"key": r.key, "plan_id": r.plan_id, "on": r.on, "fires_at": r.fires_at, "text": r.text}
                for r in agenda.due(now, window)]

    @router.post("/plans", status_code=201)
    def add(body: AddPlanBody):
        parsed = parse(body.text, schedule.local_now())
        [added] = handler.handle(AddPlan(parsed.title, parsed.day, parsed.time,
                                         body.duration_min or parsed.duration_min, body.note, parsed.repeat))
        return plan_json(agenda.get(added.plan_id))

    return router
