"""A structured overview of the whole plan, for an assistant to turn into prose.

Everything here is derived from the ledger and the calendar -- nothing is
estimated -- so the brief a partner reads carries the same numbers the other
tools give.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Set as AbstractSet
from datetime import date

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
) -> dict:
    """The plan at a glance, as of `today`.

    `windows` are school holidays as `describe_window` returns them. Each gets
    the plans that fall in its effective dates; windows still ahead with no plan
    at all are listed under `open_windows`, since those are the decisions left.
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
    for window in windows:
        start = date.fromisoformat(window["effective_start"])
        end = date.fromisoformat(window["effective_end"])
        plans = [t.id for t in active if t.start_date <= end and t.end_date >= start]
        school_windows.append({**window, "plans": plans})
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
        "family": _family(ledger, today),
        "counts": dict(Counter(t.status for t in ledger.trips)),
        "years": years,
        "trips": trips,
        "school_windows": school_windows,
        "open_windows": open_windows,
        "issues": validate_ledger(ledger, set(public_holidays), windows, half_holidays),
        "overdue": [d for d in due if d["overdue"]],
        "upcoming": [d for d in due if not d["overdue"]][:UPCOMING],
    }


def _family(ledger: Ledger, today: date) -> dict | None:
    profile = ledger.profile
    if profile is None:
        return None
    return {
        "country": profile.country,
        "region": profile.region,
        "home": profile.home,
        "max_leg_minutes": profile.max_leg_minutes,
        "children": [
            {
                "alias": c.alias,
                "age_this_year": today.year - c.birth_year if c.birth_year else None,
                "interests": c.interests,
            }
            for c in profile.children
        ],
        "visited": profile.visited,
        "excluded": profile.excluded,
    }
