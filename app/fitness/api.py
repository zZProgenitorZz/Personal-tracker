"""Fitness' JSON API. The ingest endpoints are what the iPhone (Apple Shortcuts) calls; through
Tailscale they need the bearer token PROGEN_INGEST_TOKEN (see app/auth.py)."""
import datetime as dt
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from .. import clock
from .commands import ArriveAtGym, FitnessCommandHandler, LeaveGym, RecordSteps
from .projections import GymProjection, StepsProjection

MAX_INGEST_DAYS = 31
MAX_INGEST_GYM = 50


class StepsOfDay(BaseModel):
    day: dt.date
    steps: int


class StepsBody(BaseModel):
    days: list[StepsOfDay] = Field(min_length=1, max_length=MAX_INGEST_DAYS,
                                   description="The steps per day, e.g. the last 7 days")


class GymEvent(BaseModel):
    event: Literal["arrived", "left"]
    at: dt.datetime = Field(description="ISO 8601, with an offset")


class GymBody(BaseModel):
    events: list[GymEvent] = Field(min_length=1, max_length=MAX_INGEST_GYM,
                                   description="Arrivals and departures, in any order; sent again is fine")


def create_fitness_router(handler: FitnessCommandHandler, steps: StepsProjection, gym: GymProjection) -> APIRouter:
    router = APIRouter(prefix="/fitness", tags=["fitness"])

    @router.get("/steps")
    def steps_per_day(days: int = 14):
        today = clock.today()
        return [{"day": day, "steps": n}
                for day, n in steps.between(today - dt.timedelta(days=max(days, 1) - 1), today).items()]

    @router.get("/visits")
    def visits(days: int = 30):
        today = clock.today()
        return [{"day": v.day, "start": v.start, "end": v.end, "minutes": v.minutes, "in_progress": v.in_progress}
                for v in gym.between(today - dt.timedelta(days=max(days, 1) - 1), today, clock.local_now())]

    @router.post("/ingest/steps")
    def ingest_steps(body: StepsBody):
        """Steps per day. The same number again changes nothing; a different one replaces it. All or nothing."""
        results = handler.handle_many([RecordSteps(d.day, d.steps) for d in body.days])
        recorded = sum(bool(events) for events in results)
        return {"recorded": recorded, "unchanged": len(results) - recorded}

    @router.post("/ingest/gym")
    def ingest_gym(body: GymBody):
        """Arrivals and departures, late, twice or in any order: a time that is already known changes
        nothing. All or nothing."""
        commands = [ArriveAtGym(clock.to_local(e.at)) if e.event == "arrived" else LeaveGym(clock.to_local(e.at))
                    for e in body.events]
        results = handler.handle_many(commands)
        recorded = sum(bool(events) for events in results)
        return {"recorded": recorded, "unchanged": len(results) - recorded}

    return router
