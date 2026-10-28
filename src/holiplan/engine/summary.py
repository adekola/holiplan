"""A structured overview of the whole plan, for an assistant to turn into prose.

Everything here is derived from the ledger and the calendar -- nothing is
estimated -- so the brief a partner reads carries the same numbers the other
tools give.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Set as AbstractSet
from datetime import date, timedelta

from .checks import deadlines, validate_ledger
from .leave import leave_cost, year_budget
from .models import Ledger

UPCOMING = 5  # deadlines listed beyond the overdue ones


def summarise(
    ledger: Ledger,
    public_holidays: AbstractSet[str],
    windows: list[dict],
    today: date,
    half_holidays: AbstractSet[str] = frozenset(),
    plan_year: int | None = None,
) -> dict:
    """The plan at a glance, as of `today`.

    `windows` are school holidays as `describe_window` returns them. Each gets
    the plans that fall in its effective dates; windows still ahead with no plan
    at all are listed under `open_windows`, since those are the decisions left.
    Each window also counts its `open_school_days`: school days off that no
    plan covers yet, whatever the plan's intent; `open_school_days` at the top
    sums them per calendar year. Children's `age` is for `plan_year` (default:
    `today`'s year), so a 2027 plan made in 2026 shows the ages they will be.
    """
    profile = ledger.profile
    pattern = profile.day_pattern if profile else None
    active = [t for t in ledger.trips if t.status != "cancelled"]

    trips = []
    for trip in sorted(ledger.trips, key=lambda t: t.start):
        charged = trip.consumes_leave and trip.status != "cancelled"
        cost = leave_cost(
            trip.start_date, trip.end_date, public_holidays, pattern, trip.id, half_holidays
        )
        trips.append(
            {
                "id": trip.id,
                "label": trip.label,
                "start": trip.start,
                "end": trip.end,
                "intent": trip.intent,
                "status": trip.status,
                "destination": trip.destination,
                "days_away": cost.days_away,
                "leave_days": cost.leave_days if charged else 0,
                "bookings_booked": sum(b.status == "booked" for b in trip.bookings),
                "bookings_todo": sum(b.status == "todo" for b in trip.bookings),
            }
        )

    school_windows = []
    open_days: Counter[str] = Counter()
    for window in windows:
        start = date.fromisoformat(window["effective_start"])
        end = date.fromisoformat(window["effective_end"])
        plans = [t.id for t in active if t.start_date <= end and t.end_date >= start]
        uncovered = [
            day
            for day in _school_days_off(window, public_holidays)
            if not any(t.start_date <= day <= t.end_date for t in active)
        ]
        for y in {window["start"][:4], window["end"][:4]}:
            open_days[y] += 0  # a year whose holidays are all covered still shows, as 0
        open_days.update(str(day.year) for day in uncovered)
        school_windows.append({**window, "plans": plans, "open_school_days": len(uncovered)})
    open_windows = [
        {k: w[k] for k in ("name", "effective_start", "effective_end", "days_off")}
        for w in school_windows
        if not w["plans"] and not w["closure"] and w["effective_end"] >= today.isoformat()
    ]

    years = []
    if profile:
        for account in sorted(profile.leave, key=lambda a: a.year):
            years.append(
                year_budget(
                    ledger, account.year, public_holidays, half_holidays=half_holidays
                ).as_dict()
            )

    due = deadlines(ledger, today)
    return {
        "as_of": today.isoformat(),
        "family": _family(ledger, plan_year or today.year, today),
        "counts": dict(Counter(t.status for t in ledger.trips)),
        "years": years,
        "trips": trips,
        "school_windows": school_windows,
        "open_windows": open_windows,
        "open_school_days": dict(sorted(open_days.items())),
        "issues": validate_ledger(ledger, set(public_holidays), windows, half_holidays),
        "overdue": [d for d in due if d["overdue"]],
        "upcoming": [d for d in due if not d["overdue"]][:UPCOMING],
    }


def _school_days_off(window: dict, public_holidays: AbstractSet[str]) -> list[date]:
    """The Monday-to-Friday days inside a window's official dates that aren't public holidays.

    The same days `describe_window` counts as `school_days`: the ones the
    holiday actually takes the children out of school.
    """
    start, end = date.fromisoformat(window["start"]), date.fromisoformat(window["end"])
    days = (start + timedelta(days=n) for n in range((end - start).days + 1))
    return [d for d in days if d.weekday() < 5 and d.isoformat() not in public_holidays]


def _family(ledger: Ledger, plan_year: int, today: date) -> dict | None:
    profile = ledger.profile
    if profile is None:
        return None
    return {
        "country": profile.country,
        "region": profile.region,
        "home": profile.home,
        "max_leg_minutes": profile.max_leg_minutes,
        "plan_year": plan_year,
        "children": [
            {
                "alias": c.alias,
                # The age in the plan year; age_this_year (as of today) is kept for older callers.
                "age": plan_year - c.birth_year if c.birth_year else None,
                "age_this_year": today.year - c.birth_year if c.birth_year else None,
                "interests": c.interests,
            }
            for c in profile.children
        ],
        "visited": profile.visited,
        "excluded": profile.excluded,
    }
