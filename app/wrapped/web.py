"""htmx route for Wrapped (#wrapped): the year in review. Read only.

Wrapped isn't a tracker: it isn't in the trackers list (so not in
Settings or the navigation bar), only a card on the home screen.
"""

from fastapi import APIRouter, Request

from .. import clock
from ..charts import percent
from ..web import render
from .projections import WrappedProjection, YearReview


def hours(minutes: float) -> float:
    return round(minutes / 60, 1)


def headline(review: YearReview) -> dict:
    """The big numbers at the top and on the home screen."""
    return {"chapters": review.chapters, "finished": review.finished, "hours": hours(review.listen_minutes)}


def wrapped_teaser(wrapped: WrappedProjection) -> dict:
    year = clock.today().year
    return {"year": year, **headline(wrapped.year(year))}


def delta(now: float, before: float) -> dict:
    diff = round(now - before, 1)
    return {"value": diff, "text": f"{diff:+g}", "up": diff > 0, "down": diff < 0}


def with_width(rows: list[tuple[str, float]]) -> list[dict]:
    top = max((n for _, n in rows), default=0)
    return [{"name": name, "value": n, "width": percent(n, top)} for name, n in rows]


def create_wrapped_web_router(wrapped: WrappedProjection) -> APIRouter:
    router = APIRouter(prefix="/ui", include_in_schema=False)

    @router.get("/wrapped")
    def wrapped_page(request: Request, year: str = "", compare: bool = False):
        this_year = clock.today().year
        number = int(year) if year.isdigit() and 1900 < int(year) < 3000 else this_year
        review = wrapped.year(number)
        years = wrapped.years()
        if this_year not in years:
            years = sorted({*years, this_year}, reverse=True)  # this year is always there, even when empty
        big = headline(review)
        before = headline(wrapped.year(number - 1))
        months = review.months
        tops = {key: max((getattr(m, key) for m in months), default=0) for key in ("chapters", "finished", "minutes")}
        return render(
            request, "wrapped.html",
            review=review, year=number, years=years, compare=compare, big=big,
            deltas={key: delta(big[key], before[key]) for key in big},
            reading_genres=with_width(review.reading_genres[:6]),
            watch_genres=with_width(review.watch_genres[:6]),
            artists=with_width(review.top_artists),
            months=[{"row": m, **{key: percent(getattr(m, key), tops[key]) for key in tops}} for m in months],
            hours=hours,
        )

    return router
