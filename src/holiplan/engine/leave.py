"""Leave arithmetic: what a trip costs, and what's left in the year.

Holiday dates are passed in (a set of ISO date strings) so this module stays
offline and deterministic. The OpenHolidays client supplies them at runtime.
"""

from __future__ import annotations

from collections.abc import Set as AbstractSet
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Iterable

from .models import DayPattern, FamilyProfile, Ledger, Trip


def daterange(start: date, end: date) -> Iterable[date]:
    day = start
    while day <= end:
        yield day
        day += timedelta(days=1)


@dataclass
class LeaveCost:
    trip_id: str | None
    start: str
    end: str
    days_away: int
    leave_days: float
    public_holidays_used: list[str]  # ISO dates that fell on a workday
    weekend_days: int
    half_day_holidays_used: list[str] = field(default_factory=list)  # afternoon off only

    def as_dict(self) -> dict:
        return {
            "trip_id": self.trip_id,
            "start": self.start,
            "end": self.end,
            "days_away": self.days_away,
            "leave_days": self.leave_days,
            "public_holidays_used": self.public_holidays_used,
            "weekend_days": self.weekend_days,
            "half_day_holidays_used": self.half_day_holidays_used,
        }


def leave_cost(
    start: date,
    end: date,
    public_holidays: set[str],
    pattern: DayPattern | None = None,
    trip_id: str | None = None,
    half_holidays: AbstractSet[str] = frozenset(),
) -> LeaveCost:
    """Leave days needed to be away from `start` to `end` inclusive.

    `half_holidays` are public holidays that free only half the day (Zürich's
    Sechseläuten), so a workday on one costs at most half a day of leave.
    """
    pattern = pattern or DayPattern()
    total = 0.0
    used: list[str] = []
    half_used: list[str] = []
    weekend = 0
    days = 0

    for day in daterange(start, end):
        days += 1
        iso = day.isoformat()
        if not pattern.is_workday(day):
            weekend += 1
            continue
        if iso in public_holidays:
            used.append(iso)
            continue
        weight = pattern.leave_weight(day)
        if iso in half_holidays:
            half_used.append(iso)
            weight = min(weight, 0.5)
        total += weight

    return LeaveCost(
        trip_id=trip_id,
        start=start.isoformat(),
        end=end.isoformat(),
        days_away=days,
        leave_days=total,
        public_holidays_used=used,
        weekend_days=weekend,
        half_day_holidays_used=half_used,
    )


def _days(n: float) -> str:
    """A day count for a sentence: 4, not 4.0; 4.5 stays 4.5."""
    return f"{n:g}"


@dataclass
class YearBudget:
    year: int
    allowance: float
    carryover: float
    carryover_expires: str | None
    planned: float
    carryover_used_before_expiry: float
    remaining: float
    carryover_at_risk: float
    warnings: list[str]  # English sentences, one per notice
    # The same, structured: {"level", "code", "params"} for front ends that word
    # them themselves (in another language, say). Codes: over_budget (error;
    # days), carryover_at_risk (warning; days, expires).
    notices: list[dict]

    def as_dict(self) -> dict:
        return self.__dict__.copy()


def year_budget(
    ledger: Ledger,
    year: int,
    public_holidays: set[str],
    profile: FamilyProfile | None = None,
    half_holidays: AbstractSet[str] = frozenset(),
) -> YearBudget:
    """Leave planned versus available for one year, including carryover expiry.

    Carryover is treated as spent first, but only by trips that finish on or
    before the expiry date -- that is what makes "use it before June" visible.
    """
    profile = profile or ledger.profile
    if profile is None:
        raise ValueError("ledger has no profile; pass one explicitly")

    account = next((a for a in profile.leave if a.year == year), None)
    if account is None:
        raise ValueError(f"no leave account for {year}")

    expiry = date.fromisoformat(account.carryover_expires) if account.carryover_expires else None

    planned = 0.0
    before_expiry = 0.0
    warnings: list[str] = []

    first_day, last_day = date(year, 1, 1), date(year, 12, 31)
    for trip in ledger.trips:
        if trip.status == "cancelled" or not trip.consumes_leave:
            continue
        # A trip over New Year spends each year's leave only on that year's days.
        start, end = max(trip.start_date, first_day), min(trip.end_date, last_day)
        if start > end:
            continue
        cost = leave_cost(
            start, end, public_holidays, profile.day_pattern, trip.id, half_holidays
        )
        planned += cost.leave_days
        if expiry and trip.end_date <= expiry:
            before_expiry += cost.leave_days

    carryover_used = min(account.carryover, before_expiry)
    at_risk = round(account.carryover - carryover_used, 2)
    remaining = round(account.allowance + account.carryover - planned, 2)

    notices: list[dict] = []
    if remaining < 0:
        warnings.append(f"Over budget by {_days(abs(remaining))} days.")
        notices.append(
            {"level": "error", "code": "over_budget", "params": {"year": year, "days": abs(remaining)}}
        )
    if at_risk > 0 and expiry:
        warnings.append(
            f"{_days(at_risk)} carried-over day(s) expire on {expiry.isoformat()} "
            "and no planned trip uses them."
        )
        notices.append(
            {
                "level": "warning",
                "code": "carryover_at_risk",
                "params": {"year": year, "days": at_risk, "expires": expiry.isoformat()},
            }
        )

    return YearBudget(
        year=year,
        allowance=account.allowance,
        carryover=account.carryover,
        carryover_expires=account.carryover_expires,
        planned=round(planned, 2),
        carryover_used_before_expiry=round(carryover_used, 2),
        remaining=remaining,
        carryover_at_risk=at_risk,
        warnings=warnings,
        notices=notices,
    )
