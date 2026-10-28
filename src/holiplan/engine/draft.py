"""A first draft of a family's year: one trip per school holiday, as leave allows.

The draft is a starting point, not a plan: every trip comes back as an idea for
the family to keep, move or delete. The rules, kept simple so a family can
predict them:

- Only school holidays that lie inside the year are drafted. Christmas, which
  crosses New Year, stays open: many families spend it at home.
- The summer holidays (the longest holiday starting June to August) are
  drafted first and get two weeks away; every other holiday then gets one
  week, in date order.
- Within a holiday, the trip goes where it costs the least leave -- next to
  public holidays and weekends -- and, of equal choices, the earliest.
- A holiday that already has a plan is left alone, and a trip the remaining
  leave can't pay for is not drafted, so the draft never goes over budget.
"""

from __future__ import annotations

from collections.abc import Set as AbstractSet
from datetime import date, timedelta

from .leave import leave_cost, year_budget
from .models import Ledger, Trip

WEEK_AWAY = 9  # Saturday to the Sunday after: five workdays
TWO_WEEKS_AWAY = 16


def draft_year(
    ledger: Ledger,
    year: int,
    windows: list[dict],
    public_holidays: AbstractSet[str],
    half_holidays: AbstractSet[str] = frozenset(),
) -> list[Trip]:
    """Draft trips for `year`, cheapest first within each holiday, within the year's leave.

    `windows` are school holidays as `describe_window` returns them (with
    effective dates and `closure`). Returns the new trips only, in date order;
    the ledger is not changed. Without a profile or a leave account for the
    year there is nothing to draft against, and nothing is returned.
    """
    profile = ledger.profile
    if profile is None or not any(a.year == year for a in profile.leave):
        return []
    pattern = profile.day_pattern
    left = year_budget(ledger, year, set(public_holidays), half_holidays=half_holidays).remaining
    active = [t for t in ledger.trips if t.status != "cancelled"]

    first, last = date(year, 1, 1), date(year, 12, 31)
    open_windows = []
    for w in windows:
        start, end = date.fromisoformat(w["effective_start"]), date.fromisoformat(w["effective_end"])
        if w.get("closure") or start < first or end > last:
            continue
        if any(t.start_date <= end and t.end_date >= start for t in active):
            continue
        open_windows.append((w, start, end))
    if not open_windows:
        return []

    summers = [o for o in open_windows if o[1].month in (6, 7, 8)]
    summer = max(summers, key=lambda o: ((o[2] - o[1]).days, -o[1].toordinal()), default=None)
    order = ([summer] if summer else []) + [o for o in open_windows if o is not summer]

    drafted: list[Trip] = []
    for chosen in order:
        w, start, end = chosen
        length = min(TWO_WEEKS_AWAY if chosen is summer else WEEK_AWAY, (end - start).days + 1)
        best = None
        for offset in range((end - start).days - length + 2):
            s = start + timedelta(days=offset)
            e = s + timedelta(days=length - 1)
            cost = leave_cost(s, e, set(public_holidays), pattern, half_holidays=half_holidays).leave_days
            if best is None or cost < best[0]:
                best = (cost, s, e)
        cost, s, e = best
        if cost > left:
            continue
        left -= cost
        drafted.append(
            Trip(
                id=f"draft-{s.isoformat()}",
                label=w["name"] or "School holidays",
                start=s.isoformat(),
                end=e.isoformat(),
                intent="trip",
                status="idea",
                window=w["name"],
            )
        )
    return sorted(drafted, key=lambda t: t.start)
