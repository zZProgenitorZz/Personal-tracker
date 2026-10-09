"""htmx-route voor Wrapped (#wrapped): het jaaroverzicht. Alleen lezen.

Wrapped is geen tracker: hij staat niet in de trackers-lijst (dus niet in
Settings of de navigatiebalk), alleen als kaart op het startscherm.
"""
from datetime import date

from fastapi import APIRouter, Request

from ..web import render
from .projections import WrappedProjection, YearReview


def hours(minutes: float) -> float:
    return round(minutes / 60, 1)


def headline(review: YearReview) -> dict:
    """De grote getallen bovenaan en op het startscherm."""
    return {"chapters": review.chapters, "finished": review.finished, "hours": hours(review.listen_minutes)}


def wrapped_teaser(wrapped: WrappedProjection) -> dict:
    year = date.today().year
    return {"year": year, **headline(wrapped.year(year))}


def delta(now: float, before: float) -> dict:
    diff = round(now - before, 1)
    return {"value": diff, "text": f"{diff:+g}", "up": diff > 0, "down": diff < 0}


def with_width(rows: list[tuple[str, float]]) -> list[dict]:
    top = max((n for _, n in rows), default=0)
    return [{"name": name, "value": n, "width": round(100 * n / top) if top else 0} for name, n in rows]


def create_wrapped_web_router(wrapped: WrappedProjection) -> APIRouter:
    router = APIRouter(prefix="/ui", include_in_schema=False)

    @router.get("/wrapped")
    def wrapped_page(request: Request, year: str = "", compare: bool = False):
        this_year = date.today().year
        number = int(year) if year.isdigit() and 1900 < int(year) < 3000 else this_year
        review = wrapped.year(number)
        years = wrapped.years()
        if this_year not in years:
            years = sorted({*years, this_year}, reverse=True)  # dit jaar staat er altijd, ook leeg
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
            months=[{"row": m, **{key: round(100 * getattr(m, key) / tops[key]) if tops[key] else 0
                                  for key in tops}} for m in months],
            hours=hours,
        )

    return router
