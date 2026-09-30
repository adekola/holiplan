"""Constraint checks over a ledger, plus the deadlines it implies."""

from __future__ import annotations

from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from datetime import date, timedelta

from .leave import year_budget
from .models import DayPattern, Ledger

_MAX_STRETCH = 14  # days; real stretches are a weekend plus a holiday or two


def effective_window(
    start: date,
    end: date,
    public_holidays: set[str],
    pattern: DayPattern | None = None,
) -> tuple[date, date]:
    """Stretch a school-holiday window over the free days that touch it.

    Official windows usually run Monday to Friday, but families leave on the
    Saturday before and come back on the Sunday after, and a public holiday on
    either edge (Whit Monday before Bavaria's Pentecost window) adds a day more.
    Walk outwards from each edge while the neighbouring day is a weekend or a
    public holiday, at most `_MAX_STRETCH` days, so a pattern with no workdays
    cannot walk forever.
    """
    pattern = pattern or DayPattern()

    def free(day: date) -> bool:
        return not pattern.is_workday(day) or day.isoformat() in public_holidays

    for _ in range(_MAX_STRETCH):
        if not free(start - timedelta(days=1)):
            break
        start -= timedelta(days=1)
    for _ in range(_MAX_STRETCH):
        if not free(end + timedelta(days=1)):
            break
        end += timedelta(days=1)
    return start, end


# A school holiday with this many school days or fewer is a closure (All Souls'
# Day in Austria, Zürich's Ascension bridge), not a window to plan a trip around.
CLOSURE_MAX_SCHOOL_DAYS = 2


def describe_window(
    name: str | None,
    start: date,
    end: date,
    public_holidays: set[str],
    groups: list[str] | None = None,
) -> dict:
    """A school holiday as the family sees it: raw dates, stretched dates, size.

    `school_days` counts the Monday-to-Friday days inside the raw window that
    aren't public holidays -- the days the holiday actually takes off school.
    `groups` lists the school types it applies to where the data says (Zürich
    splits primary, secondary and vocational); empty means every school.
    """
    eff_start, eff_end = effective_window(start, end, public_holidays)
    school_days = sum(
        1
        for offset in range((end - start).days + 1)
        if (day := start + timedelta(days=offset)).weekday() < 5
        and day.isoformat() not in public_holidays
    )
    return {
        "name": name,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "effective_start": eff_start.isoformat(),
        "effective_end": eff_end.isoformat(),
        "days_off": (eff_end - eff_start).days + 1,
        "school_days": school_days,
        "closure": school_days <= CLOSURE_MAX_SCHOOL_DAYS,
        "groups": groups or [],
    }


@dataclass
class Issue:
    level: str  # "error" | "warning"
    code: str
    message: str
    trip_ids: list[str]

    def as_dict(self) -> dict:
        return self.__dict__.copy()


def validate_ledger(
    ledger: Ledger,
    public_holidays: set[str],
    windows: list[dict] | None = None,
    half_holidays: AbstractSet[str] = frozenset(),
) -> list[dict]:
    """Overlaps, budget breaches, bad dates, and trips outside school windows.

    `windows` is an optional list of {"name", "start", "end"} school-holiday
    windows; trips with intent "trip" that fall outside all of them are flagged
    as a warning, since that usually means taking the kids out of school.
    """
    issues: list[Issue] = []
    trips = [t for t in ledger.trips if t.status != "cancelled"]

    for trip in trips:
        if trip.end_date < trip.start_date:
            issues.append(
                Issue("error", "bad_dates", f"{trip.label}: end is before start.", [trip.id])
            )

    ordered = sorted(trips, key=lambda t: t.start_date)
    for earlier, later in zip(ordered, ordered[1:]):
        if later.start_date <= earlier.end_date:
            issues.append(
                Issue(
                    "error",
                    "overlap",
                    f"{earlier.label} overlaps {later.label}.",
                    [earlier.id, later.id],
                )
            )

    if ledger.profile:
        account_years = {a.year for a in ledger.profile.leave}
        for trip in trips:
            if not trip.consumes_leave or trip.end_date < trip.start_date:
                continue
            missing = sorted(
                y for y in range(trip.start_date.year, trip.end_date.year + 1)
                if y not in account_years
            )
            if missing:
                years = ", ".join(str(y) for y in missing)
                issues.append(
                    Issue(
                        "warning",
                        "no_leave_account",
                        f"{trip.label} takes leave in {years}, which has no leave account.",
                        [trip.id],
                    )
                )
        for account in ledger.profile.leave:
            budget = year_budget(
                ledger, account.year, public_holidays, half_holidays=half_holidays
            )
            for warning in budget.warnings:
                level = "error" if warning.startswith("Over budget") else "warning"
                code = "over_budget" if level == "error" else "carryover_at_risk"
                issues.append(Issue(level, code, warning, []))

    if windows:
        pattern = ledger.profile.day_pattern if ledger.profile else None
        spans = []
        for w in windows:
            start, end = effective_window(
                date.fromisoformat(w["start"]),
                date.fromisoformat(w["end"]),
                public_holidays,
                pattern,
            )
            spans.append((start, end, w.get("name", "window")))
        for trip in trips:
            if trip.intent != "trip":
                continue
            inside = any(
                trip.start_date >= start and trip.end_date <= end for start, end, _ in spans
            )
            if not inside:
                issues.append(
                    Issue(
                        "warning",
                        "outside_school_window",
                        f"{trip.label} is not fully inside a school holiday window.",
                        [trip.id],
                    )
                )

    return [i.as_dict() for i in issues]


def deadlines(ledger: Ledger, today: date | None = None) -> list[dict]:
    """Every dated action the ledger implies, soonest first."""
    today = today or date.today()
    out: list[dict] = []

    for trip in ledger.trips:
        if trip.status == "cancelled":
            continue
        for booking in trip.bookings:
            if booking.book_by and booking.status == "todo":
                out.append(
                    {
                        "due": booking.book_by,
                        "kind": "book_by",
                        "trip_id": trip.id,
                        "message": f"Book {booking.what} for {trip.label}.",
                        "overdue": date.fromisoformat(booking.book_by) < today,
                    }
                )
            if booking.cancel_by and booking.status == "booked":
                out.append(
                    {
                        "due": booking.cancel_by,
                        "kind": "cancel_by",
                        "trip_id": trip.id,
                        "message": (
                            f"Free cancellation for {booking.what} "
                            f"({trip.label}) ends {booking.cancel_by}."
                        ),
                        "overdue": date.fromisoformat(booking.cancel_by) < today,
                    }
                )
        # A week at home needs nothing booked; every other plan does.
        if trip.status in ("idea", "locked") and not trip.bookings and trip.intent != "home":
            out.append(
                {
                    "due": trip.start,
                    "kind": "unbooked",
                    "trip_id": trip.id,
                    "message": f"{trip.label} has no bookings recorded.",
                    "overdue": trip.start_date < today,
                }
            )

    return sorted(out, key=lambda d: d["due"])
