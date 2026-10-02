"""holiplan's operations, for any front end: the MCP server, a web API, a script.

Each function takes and returns plain JSON-shaped data -- ledgers as dicts,
dates as ISO strings -- so callers need nothing from the engine's models. This
is where holiday data meets the engine: regions are resolved, the right public
holidays and school windows are fetched, and the profile's local-holiday
settings are applied, the same way for every caller.

Errors meant for the user are ValueError (bad input, unknown region, malformed
ledger) or holidays.HolidayDataUnavailable (OpenHolidays is down); front ends
show their messages as they are. Anything else is a bug.
"""

from __future__ import annotations

import json
import re
from datetime import date
from typing import Any

from . import holidays as oh
from .engine import bridges, checks, ics, leave, summary
from .engine.models import SCHEMA_VERSION, FamilyProfile, Ledger, Trip

SOURCE = "OpenHolidays (CC BY 4.0)"

_COORDINATES = re.compile(r"\s*-?\d+(\.\d+)?\s*,\s*-?\d+(\.\d+)?\s*")


# Holiday data, shaped for the engine


def language_code(language: str | None) -> str:
    """OpenHolidays' language code ("EN", "DE"), from any case."""
    code = (language or "EN").strip().upper()
    if not re.fullmatch(r"[A-Z]{2}", code):
        raise ValueError(f"language must be a two-letter code such as EN or DE, not {language!r}.")
    return code


def home_locality(country: str, region: str | None, home: str | None) -> bool | str:
    """Which local holidays a home observes, in `holidays.holiday_dates`'s `local` terms.

    A home naming a municipality the data knows (Augsburg) observes that
    municipality's local holidays; a home it doesn't know (Munich) observes none.
    Without a home, or with bare coordinates, there is nothing to go on, so all
    local holidays are kept.
    """
    if not home or _COORDINATES.fullmatch(home):
        return True
    return oh.locate(home, oh.subdivisions(country), region) or False


def holiday_sets(
    country: str,
    region: str | None,
    start: str,
    end: str,
    profile: FamilyProfile | None = None,
) -> tuple[set[str], set[str]]:
    """Public holidays as (full days, half days) of ISO dates.

    Both are filtered by the profile's local-holiday settings. `region` must
    already be resolved.
    """
    entries = oh.public_holidays(country, start, end, region)
    local: bool | str = True
    if profile and profile.local_holidays == "exclude":
        local = False
    elif profile and profile.local_holidays == "auto":
        local = home_locality(country, region, profile.home)
    ignore = profile.ignore_holidays if profile else ()
    return (
        oh.holiday_dates(entries, region, local, ignore),
        oh.holiday_dates(entries, region, local, ignore, half_days=True),
    )


def padded(first_year: int, last_year: int) -> tuple[str, str]:
    """A date range for public holidays: whole years plus the month either side.

    Windows are stretched over holidays just beyond them -- the Christmas window
    runs into January -- so the stretch needs the neighbouring holidays too.
    """
    return f"{first_year - 1}-12-01", f"{last_year + 1}-01-31"


def school_windows(
    country: str, region: str | None, start: str, end: str, language: str = "EN"
) -> list[dict]:
    """School holidays as {name, start, end, groups}, one entry per distinct window.

    Where holidays differ by school type the data repeats a window once per
    type group (Zürich: primary, secondary, vocational); identical repeats are
    merged and their groups combined. An entry without groups applies to all.
    """
    merged: dict[tuple, dict] = {}
    for entry in oh.school_holidays(country, start, end, region, language_code(language)):
        name = (entry.get("name") or [{}])[0].get("text")
        window = (name, entry["startDate"], entry.get("endDate", entry["startDate"]))
        groups = [g["code"] for g in entry.get("groups", []) if g.get("code")]
        seen = merged.get(window)
        if seen is None:
            merged[window] = {"name": name, "start": window[1], "end": window[2], "groups": groups}
        elif seen["groups"]:  # an empty list already means every school
            seen["groups"] = sorted(set(seen["groups"]) | set(groups)) if groups else []
    return sorted(merged.values(), key=lambda w: (w["start"], w["end"]))


def described_windows(
    country: str,
    region: str | None,
    start: str,
    end: str,
    full_holidays: set[str],
    language: str = "EN",
) -> list[dict]:
    """School holidays between `start` and `end`, with effective dates and size."""
    return [
        checks.describe_window(
            w["name"],
            date.fromisoformat(w["start"]),
            date.fromisoformat(w["end"]),
            full_holidays,
            w["groups"],
        )
        for w in school_windows(country, region, start, end, language)
    ]


def _profile(ledger: dict | None) -> FamilyProfile | None:
    return Ledger.from_dict(ledger).profile if ledger else None


def _validate(ledger: Ledger) -> list[dict]:
    """Run every check, fetching holidays for the whole years the trips touch.

    Whole years rather than the trips' own span, because school windows are
    stretched over holidays just outside them. Without a profile there is no
    region to look up, so only the date checks (bad dates, overlaps) run.
    """
    if not ledger.trips:
        return []
    profile = ledger.profile
    if profile is None:
        return checks.validate_ledger(ledger, set())
    region = oh.resolve_region(profile.country, profile.region)
    first = min(min(t.start_date, t.end_date) for t in ledger.trips).year
    last = max(max(t.start_date, t.end_date) for t in ledger.trips).year
    full, half = holiday_sets(profile.country, region, *padded(first, last), profile)
    windows = school_windows(profile.country, region, f"{first}-01-01", f"{last}-12-31")
    return checks.validate_ledger(ledger, full, windows, half)


# Operations


def holiday_windows(
    country: str,
    year: int,
    region: str | None = None,
    home: str | None = None,
    language: str = "EN",
) -> dict[str, Any]:
    """School windows (official and effective), closures, and public holidays for a year.

    Names are in `language` where OpenHolidays has them. Public holidays carry
    `local` (applies to only part of the region), `observed` (whether `home`
    keeps it; null without a home) and `half_day`.
    """
    language = language_code(language)
    region = oh.resolve_region(country, region)
    start, end = f"{year}-01-01", f"{year}-12-31"
    around = oh.public_holidays(country, *padded(year, year), region, language)
    public = [e for e in around if start <= e["startDate"] <= end]
    locality: bool | str | None = None
    if home:
        locality = home_locality(country, region, home)
        if locality is True:  # coordinates: nothing to check against
            locality = None

    def observed(entry: dict) -> bool | None:
        if not oh.is_local(entry, region):
            return True
        if locality is None:
            return None
        return locality is not False and oh.covers(entry, locality)

    free = oh.holiday_dates(around, region, True if locality is None else locality)
    described = described_windows(country, region, start, end, free, language)
    return {
        "country": country,
        "region": region,
        "year": year,
        "language": language,
        "school_windows": [w for w in described if not w["closure"]],
        "school_closures": [w for w in described if w["closure"]],
        "public_holidays": [
            {
                "date": e["startDate"],
                "name": (e.get("name") or [{}])[0].get("text"),
                "local": oh.is_local(e, region),
                "observed": observed(e),
                "half_day": oh.is_half_day(e),
            }
            for e in public
        ],
        "source": SOURCE,
    }


def leave_cost(
    start: str, end: str, country: str, region: str | None = None, ledger: dict | None = None
) -> dict[str, Any]:
    """Leave days needed to be away from `start` to `end` (inclusive)."""
    profile = _profile(ledger)
    region = oh.resolve_region(country, region)
    full, half = holiday_sets(country, region, start, end, profile)
    pattern = profile.day_pattern if profile else None
    cost = leave.leave_cost(
        date.fromisoformat(start), date.fromisoformat(end), full, pattern, half_holidays=half
    )
    return cost.as_dict()


def bridge_days(
    country: str,
    year: int,
    region: str | None = None,
    ledger: dict | None = None,
    max_leave_days: float = 4,
    limit: int = 15,
) -> dict[str, Any]:
    """The leave days that buy the most time off, best value first."""
    profile = _profile(ledger)
    region = oh.resolve_region(country, region)
    full, half = holiday_sets(country, region, *padded(year, year), profile)
    windows = described_windows(country, region, f"{year}-01-01", f"{year}-12-31", full)
    options = bridges.bridge_days(
        year,
        full,
        profile.day_pattern if profile else None,
        half,
        max_leave_days,
        [
            (date.fromisoformat(w["effective_start"]), date.fromisoformat(w["effective_end"]))
            for w in windows
        ],
    )
    return {
        "year": year,
        "region": region,
        "options": [o.as_dict() for o in options[:limit]],
        "more": max(len(options) - limit, 0),
        "source": SOURCE,
    }


def year_budget(ledger: dict, year: int) -> dict[str, Any]:
    """Leave planned versus available for a year, including carryover expiry."""
    parsed = Ledger.from_dict(ledger)
    profile = parsed.profile
    if profile is None:
        raise ValueError("The ledger has no profile, so there is no leave allowance to check.")
    region = oh.resolve_region(profile.country, profile.region)
    full, half = holiday_sets(profile.country, region, f"{year}-01-01", f"{year}-12-31", profile)
    return leave.year_budget(parsed, year, full, half_holidays=half).as_dict()


def upsert_trip(ledger: dict, trip: dict) -> dict[str, Any]:
    """Add or replace one trip (matched by id), then re-check the whole ledger."""
    parsed = Ledger.from_dict(ledger)
    incoming = Trip.from_dict(trip)
    parsed.trips = [t for t in parsed.trips if t.id != incoming.id] + [incoming]
    parsed.trips.sort(key=lambda t: t.start)
    return {"ledger": parsed.to_dict(), "issues": _validate(parsed)}


def check_ledger(ledger: dict) -> dict[str, Any]:
    """Overlaps, budget breaches, bad dates, and trips outside the school holidays."""
    parsed = Ledger.from_dict(ledger)
    return {"issues": _validate(parsed), "checked_trips": len(parsed.trips)}


def deadlines(ledger: dict, today: str | None = None) -> list[dict]:
    """Booking deadlines, cancel-by dates and unbooked plans, soonest first."""
    parsed = Ledger.from_dict(ledger)
    return checks.deadlines(parsed, date.fromisoformat(today) if today else None)


def summarise_plan(ledger: dict, today: str | None = None, language: str = "EN") -> dict[str, Any]:
    """The whole plan at a glance: family, leave, trips, windows, issues, deadlines."""
    parsed = Ledger.from_dict(ledger)
    as_of = date.fromisoformat(today) if today else date.today()
    profile = parsed.profile
    if profile is None:
        return summary.summarise(parsed, set(), [], as_of)
    region = oh.resolve_region(profile.country, profile.region)
    years = {a.year for a in profile.leave} | {
        y for t in parsed.trips for y in (t.start_date.year, t.end_date.year)
    } or {as_of.year}
    first, last = min(years), max(years)
    full, half = holiday_sets(profile.country, region, *padded(first, last), profile)
    windows = described_windows(
        profile.country, region, f"{first}-01-01", f"{last}-12-31", full, language
    )
    return summary.summarise(parsed, full, windows, as_of, half)


def calendar(ledger: dict, include_deadlines: bool = True) -> str:
    """The ledger as .ics file contents."""
    return ics.to_ics(Ledger.from_dict(ledger), include_deadlines)


# The ledger contract


def schema(part: str | None = None) -> dict[str, Any]:
    """The ledger JSON schema, or one of its parts ("profile", "trip") on its own."""
    from importlib.resources import files

    full = json.loads(
        (files("holiplan") / "schemas" / "ledger.schema.json").read_text(encoding="utf-8")
    )
    if part is None:
        return full
    return {"$schema": full["$schema"], "$id": f"urn:holiplan:{part}:1", **full["$defs"][part]}


def starter_ledger(today: date | None = None) -> dict[str, Any]:
    """A blank ledger to fill in: placeholder country, no trips, zero leave."""
    this_year = (today or date.today()).year
    return {
        "schema_version": SCHEMA_VERSION,
        "profile": {
            "country": "DE",
            "region": None,
            "home": None,
            "children": [],
            "day_pattern": {"workdays": [0, 1, 2, 3, 4], "half_days": []},
            "leave": [
                {"year": year, "allowance": 0, "carryover": 0, "carryover_expires": None}
                for year in (this_year, this_year + 1)
            ],
            "visited": [],
            "excluded": [],
        },
        "trips": [],
    }
