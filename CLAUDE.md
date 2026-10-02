# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`holiplan` is a stateless MCP server that helps an assistant plan a family's year around school
holidays and a limited leave allowance. The server does the arithmetic (leave cost, budget,
carryover expiry, bridge days, overlaps, deadlines, .ics export); the assistant does the judgement
(destinations). Holiday data comes from the OpenHolidays API (CC BY 4.0, no key). Attribution is
required wherever that data is shown, which is why tool results carry a `source` field.

This is Release 1 of a two-release product (MCP server, then a web app on the same engine). The
ledger schema is the contract between them.

## Commands

A venv already exists at `.venv` (Windows layout: `.venv/Scripts/python`).

```bash
pip install -e ".[dev]"                                   # install with pytest
python -m unittest discover -s tests                      # full suite, offline
python -m unittest tests.test_engine.EffectiveWindowTests # one class
python -m unittest tests.test_engine.BudgetTests.<test_name>  # one test
python -m holiplan.server                                 # run the server over stdio (or the `holiplan` script)
```

Tests prepend `src/` to `sys.path`, so the engine tests run without installing the package. Tests
that import the server need `mcp` installed and skip themselves otherwise; schema validation tests
need `jsonschema` (a dependency of `mcp`). `pytest` also works. There is no linter or formatter
configured.

## Architecture

Two layers with a hard boundary:

- **`src/holiplan/engine/`**: pure, offline, deterministic. `models.py` (dependency-free dataclasses,
  `migrate`, and a `_build` helper every `from_dict` goes through); `leave.py` (`leave_cost`,
  `year_budget`); `checks.py` (`validate_ledger`, `deadlines`, `effective_window`,
  `describe_window`); `bridges.py` (`bridge_days`); `summary.py` (`summarise`); `ics.py` (RFC 5545
  export). Public holidays are always **passed in**: a `set[str]` of full-day ISO dates, plus an
  optional `half_holidays` set. The engine never does I/O.
- **`src/holiplan/holidays.py`**: the OpenHolidays client, with an in-process one-week cache. On a
  failed fetch it serves the expired cache entry if there is one, otherwise raises
  `HolidayDataUnavailable` with a message an assistant can relay; a 4xx becomes a `ValueError`
  about the input. It also owns the data-shaped logic: `resolve_region`, `is_local`, `covers`,
  `locate`, `is_half_day`, and `holiday_dates` (which filters by all of them).
- **`src/holiplan/service.py`**: the operations, for every front end (the MCP server here, the
  private web app's API). JSON-shaped dicts in and out; no MCP import, so other front ends don't
  need the SDK (a test enforces this). Each operation resolves its region, fetches what it needs,
  parses the ledger into models, calls the engine and returns plain dicts. Shared helpers:
  `holiday_sets` returns `(full, half)` holiday sets filtered by the profile's local-holiday
  settings; `padded` widens a year range by a month either side, because windows stretch over
  holidays just past them (Christmas into January); `school_windows` merges the per-school-type
  repeats the data contains; `described_windows` adds effective dates and sizes; `_validate`
  backs `check_ledger` and `upsert_trip`, and falls back to date-only checks without a profile.
  Names follow an optional `language` ("EN", "DE"); the engine never sees them.
- **`src/holiplan/server.py`**: the MCP surface only: one-line tools that call `service`, plus
  resources and prompts. Tool docstrings are what assistants read, so wording changes there are
  behaviour changes. Put new logic in `service`, not here.

The server is built on **MCP SDK v2** (`mcp>=2,<3`): `from mcp.server.mcpserver import MCPServer`.
Do not use the v1 `FastMCP` import. Tools are registered with `@_tool`, not `@mcp.tool()`: SDK v2
shows the model an exception's text only for `ToolError`, and reports anything else as a bare
"Error executing tool X". `_tool` re-raises `ValueError` and `HolidayDataUnavailable` as
`ToolError`, so **errors meant for the user must be one of those two**; any other exception is
treated as a crash and its message stays on the server. Tool functions stay plain callables, so
tests call them directly (and see `ToolError`).

### The ledger contract

The ledger is a JSON document owned by the user. The server stores nothing: every tool takes the
ledger as input, and mutating tools (`upsert_trip`) return a new ledger for the assistant to hand
back. The server instructions and every prompt tell the assistant to pass the ledger everywhere
(including the optional `ledger` of `calculate_leave_cost` and `find_bridge_days`) and hand back
the whole ledger after changes.

The shape is defined twice: `engine/models.py` and `src/holiplan/schemas/ledger.schema.json`
(profile and trip in `$defs`, also served on their own as `holiplan://schema/profile` and
`/trip`). `tests/test_contract.py` fails if their fields or choices drift apart, and validates
round-tripped, starter and example ledgers against the schema. `to_dict` writes `null` for unset
optional fields, so the schema's optional fields are nullable. Both refuse unknown fields.

**Changing the ledger shape:** a new optional field with a default needs no migration. Anything
that would make an older ledger read differently needs `SCHEMA_VERSION` bumped and a
`MIGRATIONS[old_version]` entry in `models.py`; `Ledger.from_dict` runs `migrate` first.
`examples/ledger.example.json` is the sample; `my-ledger.json` is gitignored.

### OpenHolidays quirks the code relies on

- **Region codes** are OpenHolidays' own where they differ from ISO 3166-2 (Vienna `AT-WI`, not
  `AT-9`), and an unknown code silently returns only nationwide data. `resolve_region` accepts
  either form and raises on unknown ones. It reads `/Subdivisions`, so server tests patch
  `holiplan.holidays.subdivisions` (see `StubbedHolidays` in `tests/test_server.py`).
- **`nationwide` is not "applies to the whole region"**: Epiphany is `nationwide: false` yet covers
  all of DE-BY. `is_local` checks whether the `subdivisions` include the region or a parent.
- **`temporalScope: "HalfDay"`** marks half-day holidays (Zürich's Sechseläuten). They are kept out
  of the full-day set and cost at most half a day of leave; they don't stretch windows.
- **School holidays repeat per school-type group** where types differ (Zürich: `CH-ZH-VS` primary,
  `-MS`, `-BS`). Identical repeats are merged and their `groups` listed; no groups means every
  school. There is no per-child school type in the profile yet.
- **Single-day school closures** exist as short school-holiday entries (All Souls' Day in Austria).
  `describe_window` marks anything with `CLOSURE_MAX_SCHOOL_DAYS` (2) or fewer school days as a
  closure; `get_holiday_windows` lists those separately. Checks treat closures like any window.

### Domain rules that span files

- **Intent decides leave.** `Trip.consumes_leave` is true for intents `trip`/`obligation`, false for
  `home`/`camp`, and `takes_leave` overrides it. Budgeting skips non-consuming and `cancelled` trips.
  A `home` week never gets an "unbooked" deadline.
- **Leave cost**: weekends (per `DayPattern.workdays`, so part-time patterns work) and public
  holidays on workdays are free. Personal `half_days` (`"MM-DD"`) and half-day public holidays
  cost 0.5.
- **Local holidays**: profile `local_holidays` defaults to `auto`: `_home_locality` resolves `home`
  against the subdivision tree (`locate`, names in any language, strictly below the region) and
  keeps only local holidays that `covers` that town. A home the tree doesn't know (Munich) keeps
  none; no home or bare coordinates keeps all. `include`/`exclude` override, and
  `ignore_holidays` (`"MM-DD"`, for region-wide entries like Assumption Day that are really local)
  applies on top.
- **Budgets are per calendar year.** `year_budget` clips each trip to the year, so a trip over New
  Year costs each year only its own days. A trip that takes leave in a year with no `LeaveAccount`
  gets a `no_leave_account` warning.
- **Carryover** is spent first, but only by trips ending on or before `carryover_expires`. Anything
  left becomes `carryover_at_risk`. `validate_ledger` turns budget warnings into issues by string
  prefix (`"Over budget"` means error `over_budget`, anything else is warning `carryover_at_risk`),
  so changing warning text in `year_budget` changes issue classification.
- **`effective_window`** stretches an official school window outwards over adjacent weekends and
  public holidays (Whit Monday before Bavaria's Pentecost window), capped at `_MAX_STRETCH` days.
  It drives the `outside_school_window` warning, which applies only to intent `trip`.
- **Bridge days** are gaps of workdays between free runs; an option is one gap or several
  neighbouring ones within `max_leave`, and must contain a public holiday on a workday. Options
  overlap by design, ranked by days off per leave day.
- **Privacy**: children are identified by `alias`, birth year and interests only, never names.
- The server never books anything. Bookings (`todo`/`booked`, `book_by`, `cancel_by`) only drive
  `list_deadlines` and the VTODOs in the .ics export. VTODO UIDs are hashes of the deadline's
  content, so re-importing a calendar updates entries rather than duplicating them. Lines are folded
  at 75 UTF-8 octets.

### Tests

Expected figures in the golden tests are worked out by hand from the calendar, with the reasoning
in a comment. A failure means arithmetic a family relies on has broken, so fix the code, not the
expectation, unless the calendar reasoning itself was wrong.

- `test_engine.py`: the engine over real Bavaria 2027 public holidays and a realistic ledger.
- `test_holidays.py`: region resolution, local and half-day filtering, and the HTTP layer via
  httpx's `MockTransport` (caching, outages, stale cache).
- `test_server.py`: the tools, with the three OpenHolidays calls patched (`StubbedHolidays`).
- `test_golden_dach.py`: Vienna and Zürich plans against real OpenHolidays responses recorded in
  `tests/fixtures/` and replayed offline. If you re-record them, re-check every expectation.
- `test_models.py`: migrations and parse errors. `test_contract.py`: schema versus models,
  resources, prompts and the tool list.
- `test_protocol.py`: the server as a subprocess over real MCP stdio (handshake, a scenario across
  every tool, errors, resources, prompts), launched through `tests/fixture_server.py`, which swaps
  OpenHolidays for a recorded fixture. About 25 seconds; it's the only slow file. Run the rest
  with `python -m unittest tests.test_engine tests.test_server ...` when iterating.
