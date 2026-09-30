"""Bridge days: the leave days that buy the most time off.

The calendar splits into alternating runs of free days (weekends, public
holidays) and gaps of workdays. Taking leave on one gap -- or on a few
neighbouring gaps -- joins the free runs either side into one stretch off.
Every such choice within the leave limit is an option, scored by days off per
leave day. Only stretches with a public holiday on a workday count: without
one, it's an ordinary long weekend, not a bridge.
"""

from __future__ import annotations

from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from datetime import date, timedelta

from .leave import daterange
from .models import DayPattern

# How far past the year's edges to look for free days to join onto.
_MARGIN = timedelta(days=14)


@dataclass
class BridgeOption:
    leave_dates: list[str]
    leave_days: float
    start: str  # first day off
    end: str  # last day off
    days_off: int
    days_off_per_leave_day: float
    public_holidays: list[str]  # the workday holidays that make it a bridge
    school: str  # "holiday", "partly" or "term": whether the kids are off too

    def as_dict(self) -> dict:
        return self.__dict__.copy()


def bridge_days(
    year: int,
    public_holidays: AbstractSet[str],
    pattern: DayPattern | None = None,
    half_holidays: AbstractSet[str] = frozenset(),
    max_leave: float = 4,
    school_windows: list[tuple[date, date]] | None = None,
) -> list[BridgeOption]:
    """Every bridge in `year` costing at most `max_leave`, best value first.

    `public_holidays` should reach a couple of weeks past the year's edges so
    stretches around New Year are measured correctly. `school_windows` are
    effective (start, end) school holidays, used to say whether the children
    are off during each option. Options may overlap; they are alternatives.
    """
    pattern = pattern or DayPattern()
    first, last = date(year, 1, 1), date(year, 12, 31)

    def free(day: date) -> bool:
        return not pattern.is_workday(day) or day.isoformat() in public_holidays

    def weight(day: date) -> float:
        w = pattern.leave_weight(day)
        return min(w, 0.5) if day.isoformat() in half_holidays else w

    # Alternating runs: (is_free, [days]).
    runs: list[tuple[bool, list[date]]] = []
    for day in daterange(first - _MARGIN, last + _MARGIN):
        if runs and runs[-1][0] == free(day):
            runs[-1][1].append(day)
        else:
            runs.append((free(day), [day]))

    options: list[BridgeOption] = []
    for i, (is_free, _) in enumerate(runs):
        if is_free or i == 0:
            continue
        leave: list[date] = []
        for j in range(i, len(runs) - 1, 2):  # gaps i, i+2, ... with free runs between
            leave += runs[j][1]
            cost = sum(weight(d) for d in leave)
            if cost > max_leave:
                break
            if leave[0] < first or leave[-1] > last:
                continue
            span = runs[i - 1][1][0], runs[j + 1][1][-1]
            holidays = [
                d.isoformat()
                for d in daterange(*span)
                if pattern.is_workday(d) and d.isoformat() in public_holidays
            ]
            if not holidays:
                continue
            days_off = (span[1] - span[0]).days + 1
            options.append(
                BridgeOption(
                    leave_dates=[d.isoformat() for d in leave],
                    leave_days=cost,
                    start=span[0].isoformat(),
                    end=span[1].isoformat(),
                    days_off=days_off,
                    days_off_per_leave_day=round(days_off / cost, 2),
                    public_holidays=holidays,
                    school=_school(span, school_windows),
                )
            )

    options.sort(key=lambda o: (-o.days_off_per_leave_day, -o.days_off, o.start))
    return options


def _school(span: tuple[date, date], windows: list[tuple[date, date]] | None) -> str:
    windows = windows or []
    if any(start <= span[0] and span[1] <= end for start, end in windows):
        return "holiday"
    if any(start <= span[1] and span[0] <= end for start, end in windows):
        return "partly"
    return "term"
