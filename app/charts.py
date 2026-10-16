"""Data for the bar charts, shared by the trackers: values per day or week, scaled to percentages."""
from datetime import date, timedelta


def percent(n: float, top: float) -> int:
    """n as a percentage of top, rounded; 0 when there is no top."""
    return round(100 * n / top) if top else 0


def compact_number(n: float) -> str:
    """Short enough to sit above a narrow bar: 0 -> '', 999 -> '999', 8123 -> '8.1k', 12000 -> '12k'."""
    if not n:
        return ""
    if n < 1000:
        return f"{n:g}"
    return f"{n / 1000:.1f}".removesuffix(".0") + "k"


def weekday_initial(day: date) -> str:
    return day.strftime("%a")[0]


def day_of_month(day: date) -> str:
    return str(day.day)


def last_days(per_day: dict[date, float], today: date, n: int, label=weekday_initial) -> list[dict]:
    days = [today - timedelta(days=i) for i in reversed(range(n))]
    return [{"day": d, "label": label(d), "value": per_day.get(d, 0)} for d in days]


def last_weeks(per_day: dict[date, float], today: date, n: int) -> list[dict]:
    monday = today - timedelta(days=today.weekday())
    weeks = []
    for i in reversed(range(n)):
        start = monday - timedelta(weeks=i)
        total = sum(per_day.get(start + timedelta(days=d), 0) for d in range(7))
        weeks.append({"start": start, "label": f"W{start.isocalendar()[1]}", "value": total})
    return weeks


def with_height(bars: list[dict]) -> list[dict]:
    """Gives each bar a height in percent of the highest bar."""
    top = max((b["value"] for b in bars), default=0)
    for bar in bars:
        bar["height"] = percent(bar["value"], top)
    return bars


def genre_bars(entries, names) -> list[dict]:
    """How many entries per genre, most first, only genres that occur; width in percent of the largest."""
    counts = {g: sum(g in e.genres for e in entries) for g in names}
    top = max(counts.values(), default=0)
    return [
        {"genre": g, "count": n, "width": percent(n, top)}
        for g, n in sorted(counts.items(), key=lambda item: -item[1]) if n
    ]
