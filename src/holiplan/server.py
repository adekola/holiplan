"""holiplan MCP server.

Stateless by design: the ledger lives with the user as a JSON file. Every tool
takes the ledger in and hands a new one back, so nothing family-specific is
stored here.

Holiday data from OpenHolidays (https://openholidaysapi.org), CC BY 4.0.
"""

from __future__ import annotations

import functools
import json
import re
from datetime import date
from typing import Any, Callable

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from . import holidays as oh
from .engine.bridges import bridge_days
from .engine.checks import deadlines as compute_deadlines
from .engine.checks import describe_window, validate_ledger
from .engine.ics import to_ics
from .engine.leave import leave_cost, year_budget
from .engine.models import SCHEMA_VERSION, FamilyProfile, Ledger, Trip
from .engine.summary import summarise

mcp = MCPServer(
    "holiplan",
    instructions=(
        "Plan a family's year around school holidays and a limited leave allowance. "
        "Call get_holiday_windows first to see what the year offers, then use "
        "calculate_leave_cost, find_bridge_days and get_year_budget before locking "
        "anything in. "
        "The ledger is the source of truth and the server keeps no copy: read it from "
        "the user's file and pass it to every tool that takes one -- including the "
        "optional `ledger` of calculate_leave_cost and find_bridge_days, which carries "
        "the work pattern and local holidays. After any change, give the user the "
        "whole updated ledger to save. No ledger yet? Start from the resource "
        "holiplan://ledger/starter or the set_up_profile prompt. "
        "Take every date, leave figure and deadline from these tools; never estimate "
        "one. Some holidays apply only to certain towns; tools work this out from the "
        "profile's `home`, so ask the family's town if it isn't recorded. "
        "Destination ideas and research are yours to make -- these tools only "
        "calculate and check. Holiday data: OpenHolidays (CC BY 4.0)."
    ),
)


def _tool(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Register `fn` as a tool whose deliberate errors reach the assistant.

    The SDK shows the model a tool's error message only for `ToolError`; any
    other exception is treated as a crash and its text stays on the server. Our
    ValueErrors (unknown region, malformed ledger, bad date) and outage errors
    are written for the assistant to relay, so they are re-raised as ToolError.
    Anything else still counts as a crash.
    """

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except (ValueError, oh.HolidayDataUnavailable) as exc:
            raise ToolError(str(exc)) from exc

    return mcp.tool()(wrapper)


_COORDINATES = re.compile(r"\s*-?\d+(\.\d+)?\s*,\s*-?\d+(\.\d+)?\s*")


def _home_locality(country: str, region: str | None, home: str | None) -> bool | str:
    """Which local holidays a home observes, in `oh.holiday_dates`'s `local` terms.

    A home naming a municipality the data knows (Augsburg) observes that
    municipality's local holidays; a home it doesn't know (Munich) observes none.
    Without a home, or with bare coordinates, there is nothing to go on, so all
    local holidays are kept.
    """
    if not home or _COORDINATES.fullmatch(home):
        return True
    return oh.locate(home, oh.subdivisions(country), region) or False


def _holiday_set(
    country: str,
    region: str | None,
    start: str,
    end: str,
    profile: FamilyProfile | None = None,
) -> tuple[set[str], set[str]]:
    """Public holidays as (full days, half days) of ISO dates.

    Both are filtered by the profile's local-holiday settings.
    """
    entries = oh.public_holidays(country, start, end, region)
    local: bool | str = True
    if profile and profile.local_holidays == "exclude":
        local = False
    elif profile and profile.local_holidays == "auto":
        local = _home_locality(country, region, profile.home)
    ignore = profile.ignore_holidays if profile else ()
    return (
        oh.holiday_dates(entries, region, local, ignore),
        oh.holiday_dates(entries, region, local, ignore, half_days=True),
    )


def _padded(first_year: int, last_year: int) -> tuple[str, str]:
    """A date range for public holidays: whole years plus the month either side.

    Windows are stretched over holidays just beyond them -- the Christmas window
    runs into January -- so the stretch needs the neighbouring holidays too.
    """
    return f"{first_year - 1}-12-01", f"{last_year + 1}-01-31"


def _school_windows(country: str, region: str | None, start: str, end: str) -> list[dict]:
    """School holidays as {name, start, end, groups}, one entry per distinct window.

    Where holidays differ by school type the data repeats a window once per
    type group (Zürich: primary, secondary, vocational); identical repeats are
    merged and their groups combined. An entry without groups applies to all.
    """
    merged: dict[tuple, dict] = {}
    for entry in oh.school_holidays(country, start, end, region):
        name = (entry.get("name") or [{}])[0].get("text")
        window = (name, entry["startDate"], entry.get("endDate", entry["startDate"]))
        groups = [g["code"] for g in entry.get("groups", []) if g.get("code")]
        seen = merged.get(window)
        if seen is None:
            merged[window] = {"name": name, "start": window[1], "end": window[2], "groups": groups}
        elif seen["groups"]:  # an empty list already means every school
            seen["groups"] = sorted(set(seen["groups"]) | set(groups)) if groups else []
    return sorted(merged.values(), key=lambda w: (w["start"], w["end"]))


def _described_windows(
    country: str, region: str | None, start: str, end: str, full_holidays: set[str]
) -> list[dict]:
    """School holidays between `start` and `end`, with effective dates and size."""
    return [
        describe_window(
            w["name"],
            date.fromisoformat(w["start"]),
            date.fromisoformat(w["end"]),
            full_holidays,
            w["groups"],
        )
        for w in _school_windows(country, region, start, end)
    ]


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
        return validate_ledger(ledger, set())
    region = oh.resolve_region(profile.country, profile.region)
    first = min(min(t.start_date, t.end_date) for t in ledger.trips).year
    last = max(max(t.start_date, t.end_date) for t in ledger.trips).year
    full, half = _holiday_set(profile.country, region, *_padded(first, last), profile)
    windows = _school_windows(profile.country, region, f"{first}-01-01", f"{last}-12-31")
    return validate_ledger(ledger, full, windows, half)


@_tool
def get_holiday_windows(
    country: str, year: int, region: str | None = None, home: str | None = None
) -> dict[str, Any]:
    """School holiday windows and public holidays for a region and year.

    Use this to frame the year before discussing destinations. `region` is a
    subdivision code such as "DE-BY" (Bavaria) or "AT-WI" (Vienna; the ISO code
    "AT-9" works too). Pass the profile's `home` (the family's town) when known.

    Each school window has its official dates and its effective dates, stretched
    over the weekends and public holidays that touch it -- the days the family
    can actually be away. Holidays of two school days or fewer are listed
    separately as `school_closures`. Public holidays marked `local` apply to only
    part of the region; `observed` says whether the home keeps them, and is null
    when there is no home to check -- then ask the family.
    """
    region = oh.resolve_region(country, region)
    start, end = f"{year}-01-01", f"{year}-12-31"
    around = oh.public_holidays(country, *_padded(year, year), region)
    public = [e for e in around if start <= e["startDate"] <= end]
    locality: bool | str | None = None
    if home:
        locality = _home_locality(country, region, home)
        if locality is True:  # coordinates: nothing to check against
            locality = None

    def observed(entry: dict) -> bool | None:
        if not oh.is_local(entry, region):
            return True
        if locality is None:
            return None
        return locality is not False and oh.covers(entry, locality)

    free = oh.holiday_dates(around, region, True if locality is None else locality)
    described = _described_windows(country, region, start, end, free)
    return {
        "country": country,
        "region": region,
        "year": year,
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
        "source": "OpenHolidays (CC BY 4.0)",
    }


@_tool
def calculate_leave_cost(
    start: str, end: str, country: str, region: str | None = None, ledger: dict | None = None
) -> dict[str, Any]:
    """Leave days needed to be away from `start` to `end` (inclusive).

    Accounts for weekends, public holidays inside the trip, and any half days,
    part-time pattern or local-holiday settings in the ledger's profile.
    """
    profile = Ledger.from_dict(ledger).profile if ledger else None
    region = oh.resolve_region(country, region)
    full, half = _holiday_set(country, region, start, end, profile)
    pattern = profile.day_pattern if profile else None
    cost = leave_cost(
        date.fromisoformat(start), date.fromisoformat(end), full, pattern, half_holidays=half
    )
    return cost.as_dict()


@_tool
def find_bridge_days(
    country: str,
    year: int,
    region: str | None = None,
    ledger: dict | None = None,
    max_leave_days: float = 4,
    limit: int = 15,
) -> dict[str, Any]:
    """The leave days that buy the most time off, best value first.

    Each option says which days to take off, the stretch that buys, days off
    per leave day, the public holidays that make it a bridge, and `school`:
    "holiday" when the children are off school for all of it, "partly", or
    "term". Pass the ledger so the parent's work pattern, half days and
    local holidays count. Options can overlap -- they are alternatives.
    """
    profile = Ledger.from_dict(ledger).profile if ledger else None
    region = oh.resolve_region(country, region)
    full, half = _holiday_set(country, region, *_padded(year, year), profile)
    windows = _described_windows(country, region, f"{year}-01-01", f"{year}-12-31", full)
    options = bridge_days(
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
        "source": "OpenHolidays (CC BY 4.0)",
    }


@_tool
def get_year_budget(ledger: dict, year: int) -> dict[str, Any]:
    """Leave planned versus available for a year, including carryover expiry.

    Answers "can we afford all of this?" -- call it after every change.
    """
    parsed = Ledger.from_dict(ledger)
    profile = parsed.profile
    if profile is None:
        raise ValueError("ledger has no profile")
    region = oh.resolve_region(profile.country, profile.region)
    full, half = _holiday_set(profile.country, region, f"{year}-01-01", f"{year}-12-31", profile)
    return year_budget(parsed, year, full, half_holidays=half).as_dict()


@_tool
def upsert_trip(ledger: dict, trip: dict) -> dict[str, Any]:
    """Add or update one trip in the ledger, then re-check the whole ledger.

    Returns the updated ledger plus any issues. Hand the ledger back to the user
    so they can save it.
    """
    parsed = Ledger.from_dict(ledger)
    incoming = Trip.from_dict(trip)
    parsed.trips = [t for t in parsed.trips if t.id != incoming.id] + [incoming]
    parsed.trips.sort(key=lambda t: t.start)
    return {"ledger": parsed.to_dict(), "issues": _validate(parsed)}


@_tool
def check_ledger(ledger: dict) -> dict[str, Any]:
    """Validate the whole ledger: overlaps, budget breaches, bad dates, and
    trips that fall outside the school holidays.

    Run this before telling the user a plan works.
    """
    parsed = Ledger.from_dict(ledger)
    return {"issues": _validate(parsed), "checked_trips": len(parsed.trips)}


@_tool
def list_deadlines(ledger: dict, today: str | None = None) -> list[dict]:
    """Dated actions the plan implies: booking deadlines and cancel-by dates.

    Use it to answer "what needs doing next?".
    """
    parsed = Ledger.from_dict(ledger)
    return compute_deadlines(parsed, date.fromisoformat(today) if today else None)


@_tool
def summarise_plan(ledger: dict, today: str | None = None) -> dict[str, Any]:
    """The whole plan at a glance, for a brief to a partner or a quick recap.

    Returns the family, leave per year, every trip with its leave cost and
    booking state, each school window with the plans in it, `open_windows`
    still to decide, issues, and overdue and upcoming deadlines. Turn it into
    prose; don't recalculate any figure in it.
    """
    parsed = Ledger.from_dict(ledger)
    as_of = date.fromisoformat(today) if today else date.today()
    profile = parsed.profile
    if profile is None:
        return summarise(parsed, set(), [], as_of)
    region = oh.resolve_region(profile.country, profile.region)
    years = {a.year for a in profile.leave} | {
        y for t in parsed.trips for y in (t.start_date.year, t.end_date.year)
    } or {as_of.year}
    first, last = min(years), max(years)
    full, half = _holiday_set(profile.country, region, *_padded(first, last), profile)
    windows = _described_windows(
        profile.country, region, f"{first}-01-01", f"{last}-12-31", full
    )
    return summarise(parsed, full, windows, as_of, half)


@_tool
def export_ics(ledger: dict, include_deadlines: bool = True) -> str:
    """The ledger as an .ics calendar file the user can import.

    Returns file contents -- tell the user to save it and import it.
    """
    return to_ics(Ledger.from_dict(ledger), include_deadlines)


def _schema() -> dict[str, Any]:
    from importlib.resources import files

    return json.loads((files("holiplan") / "schemas" / "ledger.schema.json").read_text(encoding="utf-8"))


def _part_schema(name: str) -> dict[str, Any]:
    schema = _schema()
    return {"$schema": schema["$schema"], "$id": f"urn:holiplan:{name}:1", **schema["$defs"][name]}


@mcp.resource("holiplan://schema/ledger", mime_type="application/schema+json")
def ledger_schema() -> str:
    """JSON schema for the ledger file: schema version, profile and trips."""
    return json.dumps(_schema(), indent=2)


@mcp.resource("holiplan://schema/profile", mime_type="application/schema+json")
def profile_schema() -> str:
    """JSON schema for the family profile inside the ledger."""
    return json.dumps(_part_schema("profile"), indent=2)


@mcp.resource("holiplan://schema/trip", mime_type="application/schema+json")
def trip_schema() -> str:
    """JSON schema for one trip, including its bookings."""
    return json.dumps(_part_schema("trip"), indent=2)


def starter_ledger(today: date | None = None) -> dict[str, Any]:
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


@mcp.resource("holiplan://ledger/starter", mime_type="application/json")
def starter_ledger_resource() -> str:
    """A blank ledger to fill in: placeholder country, no trips, zero leave."""
    return json.dumps(starter_ledger(), indent=2)


_LEDGER_HABITS = (
    " Pass my ledger to every tool that takes one, and whenever it changes, give me "
    "the whole updated ledger as JSON so I can save it. Take every date and leave "
    "figure from the tools; never estimate one."
)


@mcp.prompt(title="Set up my family profile")
def set_up_profile() -> str:
    """Create the family profile and a first ledger, one question at a time."""
    return (
        "Help me set up holiplan for my family. Start from the starter ledger "
        "(resource holiplan://ledger/starter) or my existing ledger file, and ask me "
        "one question at a time: our country and region (check the region with "
        "get_holiday_windows -- it accepts OpenHolidays or ISO codes); our home town, "
        "since some holidays only apply to certain towns; each child's alias, birth "
        "year and interests (never their real names); which weekdays I work and any "
        "half days, like 24 and 31 December; my leave allowance, carryover and when "
        "carryover expires, for this year and next; the longest drive we'll do in one "
        "go; and places we've been or ruled out. Then show me the finished ledger."
        + _LEDGER_HABITS
    )


@mcp.prompt(title="Plan my year")
def plan_my_year(year: int | None = None) -> str:
    """Walk through planning a whole year of family holidays."""
    year = year or date.today().year + 1
    return (
        f"Help me plan our family holidays for {year}. Read my ledger, then call "
        "get_holiday_windows for my region with my home town. For each school "
        "holiday window, give me the effective dates and what a full week away "
        "would cost in leave, and call find_bridge_days for the best-value days "
        "outside them. Then ask me one question at a time about each window -- "
        "trip, home or camp -- and check get_year_budget before we lock anything in."
        + _LEDGER_HABITS
    )


@mcp.prompt(title="Plan this holiday window")
def plan_window(window: str = "Easter") -> str:
    """Pick a destination for one holiday window."""
    return (
        f"Let's plan the {window} window. Check its effective dates with "
        "get_holiday_windows, and my profile for the drive limit, the kids' ages and "
        "interests, and places we've already been or ruled out. Suggest three options "
        "that fit those constraints, with the drive time for each, and the leave each "
        "would cost from calculate_leave_cost. Wait for me to choose before recording "
        "anything with upsert_trip." + _LEDGER_HABITS
    )


@mcp.prompt(title="What needs doing next?")
def whats_next() -> str:
    """Deadlines and problems in the plan, most urgent first."""
    return (
        "What needs doing next for our holidays? Call list_deadlines and check_ledger "
        "on my ledger and tell me, most urgent first: anything overdue, what to book "
        "or register for and by when, free cancellation that's about to end, and any "
        "problems in the plan. Keep it to a short list I can act on." + _LEDGER_HABITS
    )


@mcp.prompt(title="Make a brief for my partner")
def partner_brief() -> str:
    """A short, plain summary of the plan for the other parent."""
    return (
        "Write a brief of our holiday plan for my partner, who hasn't been part of "
        "the planning. Call summarise_plan on my ledger and turn it into a short "
        "message: what's booked, what's decided but not booked, which school holidays "
        "are still open, how much leave that uses and what's left, and the decisions "
        "or dates I need their input on. Use the numbers exactly as the tool gives "
        "them. Refer to the children by alias only." + _LEDGER_HABITS
    )


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
