"""holiplan MCP server.

Stateless by design: the ledger lives with the user as a JSON file. Every tool
takes the ledger in and hands a new one back, so nothing family-specific is
stored here. The tools are thin wrappers over `holiplan.service`, which any
other front end (such as a web API) calls the same way.

Holiday data from OpenHolidays (https://openholidaysapi.org), CC BY 4.0.
"""

from __future__ import annotations

import functools
import json
from datetime import date
from typing import Any, Callable

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from . import holidays as oh
from . import service

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


@_tool
def get_holiday_windows(
    country: str, year: int, region: str | None = None, home: str | None = None, language: str = "EN"
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
    when there is no home to check -- then ask the family. Names come in
    `language` ("EN", "DE", ...) where OpenHolidays has them.
    """
    return service.holiday_windows(country, year, region, home, language)


@_tool
def calculate_leave_cost(
    start: str, end: str, country: str, region: str | None = None, ledger: dict | None = None
) -> dict[str, Any]:
    """Leave days needed to be away from `start` to `end` (inclusive).

    Accounts for weekends, public holidays inside the trip, and any half days,
    part-time pattern or local-holiday settings in the ledger's profile.
    """
    return service.leave_cost(start, end, country, region, ledger)


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
    return service.bridge_days(country, year, region, ledger, max_leave_days, limit)


@_tool
def get_year_budget(ledger: dict, year: int) -> dict[str, Any]:
    """Leave planned versus available for a year, including carryover expiry.

    Answers "can we afford all of this?" -- call it after every change.
    """
    return service.year_budget(ledger, year)


@_tool
def upsert_trip(ledger: dict, trip: dict) -> dict[str, Any]:
    """Add or update one trip in the ledger, then re-check the whole ledger.

    Returns the updated ledger plus any issues. Hand the ledger back to the user
    so they can save it.
    """
    return service.upsert_trip(ledger, trip)


@_tool
def check_ledger(ledger: dict) -> dict[str, Any]:
    """Validate the whole ledger: overlaps, budget breaches, bad dates, and
    trips that fall outside the school holidays.

    Run this before telling the user a plan works.
    """
    return service.check_ledger(ledger)


@_tool
def list_deadlines(ledger: dict, today: str | None = None) -> list[dict]:
    """Dated actions the plan implies: booking deadlines and cancel-by dates.

    Use it to answer "what needs doing next?".
    """
    return service.deadlines(ledger, today)


@_tool
def summarise_plan(ledger: dict, today: str | None = None, language: str = "EN") -> dict[str, Any]:
    """The whole plan at a glance, for a brief to a partner or a quick recap.

    Returns the family, leave per year, every trip with its leave cost and
    booking state, each school window with the plans in it, `open_windows`
    still to decide, issues, and overdue and upcoming deadlines. Turn it into
    prose; don't recalculate any figure in it.
    School holiday names come in `language` ("EN", "DE", ...).
    """
    return service.summarise_plan(ledger, today, language)


@_tool
def export_ics(ledger: dict, include_deadlines: bool = True) -> str:
    """The ledger as an .ics calendar file the user can import.

    Returns file contents -- tell the user to save it and import it.
    """
    return service.calendar(ledger, include_deadlines)


@mcp.resource("holiplan://schema/ledger", mime_type="application/schema+json")
def ledger_schema() -> str:
    """JSON schema for the ledger file: schema version, profile and trips."""
    return json.dumps(service.schema(), indent=2)


@mcp.resource("holiplan://schema/profile", mime_type="application/schema+json")
def profile_schema() -> str:
    """JSON schema for the family profile inside the ledger."""
    return json.dumps(service.schema("profile"), indent=2)


@mcp.resource("holiplan://schema/trip", mime_type="application/schema+json")
def trip_schema() -> str:
    """JSON schema for one trip, including its bookings."""
    return json.dumps(service.schema("trip"), indent=2)


starter_ledger = service.starter_ledger


@mcp.resource("holiplan://ledger/starter", mime_type="application/json")
def starter_ledger_resource() -> str:
    """A blank ledger to fill in: placeholder country, no trips, zero leave."""
    return json.dumps(service.starter_ledger(), indent=2)


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
