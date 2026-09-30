"""Core data models.

Deliberately dependency-free: plain dataclasses plus dict (de)serialisation, so the
engine can be unit-tested without installing anything.
"""

from __future__ import annotations

from dataclasses import MISSING, asdict, dataclass, field, fields
from datetime import date
from typing import Any, Callable, Literal, get_args

SCHEMA_VERSION = "1"

# Upgrades from one schema version to the next, keyed by the version they
# upgrade from: MIGRATIONS["1"] turns a version-1 ledger dict into version 2.
# Bump SCHEMA_VERSION and add an entry here whenever a change would make an
# older ledger read differently, so saved ledgers keep working.
MIGRATIONS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {}

TripStatus = Literal["idea", "locked", "booked", "cancelled"]
TripIntent = Literal["trip", "home", "camp", "obligation"]
BookingStatus = Literal["todo", "booked"]
LocalHolidays = Literal["auto", "include", "exclude"]


def _d(value: str | date) -> date:
    return value if isinstance(value, date) else date.fromisoformat(value)


def migrate(data: dict[str, Any]) -> dict[str, Any]:
    """Bring a ledger dict up to SCHEMA_VERSION, one migration at a time.

    A ledger without a version is taken to be version 1. One newer than this
    code understands is refused rather than half-read.
    """
    _object(data, "The ledger")
    version = str(data.get("schema_version", "1"))
    if not version.isdigit():
        raise ValueError(f"schema_version must be a number like \"1\", not {version!r}.")
    if int(version) > int(SCHEMA_VERSION):
        raise ValueError(
            f"This ledger uses schema version {version}, but this holiplan server only "
            f"understands up to version {SCHEMA_VERSION}. Update the server."
        )
    while int(version) < int(SCHEMA_VERSION):
        data = MIGRATIONS[version](dict(data))
        version = str(int(version) + 1)
    return {**data, "schema_version": SCHEMA_VERSION}


def _object(data: Any, where: str) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError(f"{where} should be a JSON object, not {type(data).__name__}.")
    return data


def _build(cls, data: Any, where: str, choices=None, dates=(), **nested):
    """`cls(**data)`, with errors an assistant can act on.

    Unknown fields are refused rather than dropped, so nothing the user wrote
    silently disappears when the ledger is handed back.
    """
    _object(data, where)
    known = [f.name for f in fields(cls)]
    unknown = sorted(set(data) - set(known))
    if unknown:
        raise ValueError(
            f"{where} has unknown field(s) {', '.join(unknown)}. "
            f"Known fields: {', '.join(known)}."
        )
    missing = [
        f.name
        for f in fields(cls)
        if f.default is MISSING and f.default_factory is MISSING and data.get(f.name) is None
    ]
    if missing:
        raise ValueError(f"{where} is missing {', '.join(missing)}.")
    for name, allowed in (choices or {}).items():
        if name in data and data[name] not in allowed:
            raise ValueError(
                f"{where}: {name} must be one of {', '.join(allowed)}, not {data[name]!r}."
            )
    for name in dates:
        if data.get(name) is not None:
            try:
                date.fromisoformat(data[name])
            except (TypeError, ValueError):
                raise ValueError(
                    f"{where}: {name} must be a date like 2027-03-20, not {data[name]!r}."
                ) from None
    return cls(**{**data, **nested})


@dataclass
class DayPattern:
    """Which weekdays the person normally works, plus employer-specific half days."""

    workdays: list[int] = field(default_factory=lambda: [0, 1, 2, 3, 4])  # Mon=0
    half_days: list[str] = field(default_factory=list)  # e.g. ["12-24", "12-31"]

    def is_workday(self, day: date) -> bool:
        return day.weekday() in self.workdays

    def leave_weight(self, day: date) -> float:
        return 0.5 if f"{day.month:02d}-{day.day:02d}" in self.half_days else 1.0


@dataclass
class Child:
    alias: str  # never a real name; "older", "younger", "A"
    birth_year: int | None = None
    interests: list[str] = field(default_factory=list)


@dataclass
class LeaveAccount:
    year: int
    allowance: float
    carryover: float = 0.0
    carryover_expires: str | None = None  # ISO date


@dataclass
class FamilyProfile:
    country: str  # ISO 3166-1 alpha-2, e.g. "DE"
    region: str | None = None  # subdivision, e.g. "DE-BY"
    home: str | None = None  # free text or "lat,lon"
    max_leg_minutes: int | None = None
    children: list[Child] = field(default_factory=list)
    day_pattern: DayPattern = field(default_factory=DayPattern)
    leave: list[LeaveAccount] = field(default_factory=list)
    visited: list[str] = field(default_factory=list)
    excluded: list[str] = field(default_factory=list)
    # Holidays that apply only to part of `region` (Augsburg's Peace Festival in DE-BY).
    # "auto" keeps those that cover `home`; with no usable home it keeps them all.
    local_holidays: LocalHolidays = "auto"
    # "MM-DD" dates to treat as workdays even though the data lists them for the whole
    # region -- Assumption Day in DE-BY is only a holiday in Catholic-majority towns.
    ignore_holidays: list[str] = field(default_factory=list)

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "FamilyProfile":
        _object(data, "The profile")
        return _build(
            FamilyProfile,
            data,
            "The profile",
            choices={"local_holidays": get_args(LocalHolidays)},
            children=[
                _build(Child, c, f"Child {i + 1}") for i, c in enumerate(data.get("children") or [])
            ],
            day_pattern=_build(DayPattern, data.get("day_pattern") or {}, "The day pattern"),
            leave=[
                _build(LeaveAccount, a, f"Leave account {i + 1}", dates=("carryover_expires",))
                for i, a in enumerate(data.get("leave") or [])
            ],
        )


@dataclass
class Booking:
    what: str  # "accommodation", "course", "ferry"
    status: BookingStatus = "todo"
    reference: str | None = None
    cancel_by: str | None = None  # ISO date
    book_by: str | None = None  # ISO date, e.g. a camp registration deadline
    note: str | None = None


@dataclass
class Trip:
    id: str
    label: str
    start: str  # ISO date, first day away (or first day of the home week)
    end: str  # ISO date, last day away, inclusive
    intent: TripIntent = "trip"
    status: TripStatus = "idea"
    destination: str | None = None
    window: str | None = None  # school-holiday window this belongs to
    bookings: list[Booking] = field(default_factory=list)
    notes: str | None = None
    takes_leave: bool | None = None  # None -> derive from intent

    @property
    def consumes_leave(self) -> bool:
        """Whether this entry costs the parent leave days.

        Home weeks and camp weeks occupy a school holiday window without anyone
        taking time off, so they belong in the ledger but not in the budget.
        Set `takes_leave` explicitly to override (a home week where you do take
        leave, or an obligation you attend without booking time off).
        """
        if self.takes_leave is not None:
            return self.takes_leave
        return self.intent in ("trip", "obligation")

    @property
    def start_date(self) -> date:
        return _d(self.start)

    @property
    def end_date(self) -> date:
        return _d(self.end)

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "Trip":
        trip_id = _object(data, "A trip").get("id", "(no id)")
        where = f"Trip {trip_id!r}"
        return _build(
            Trip,
            data,
            where,
            choices={"intent": get_args(TripIntent), "status": get_args(TripStatus)},
            dates=("start", "end"),
            bookings=[
                _build(
                    Booking,
                    b,
                    f"Booking {i + 1} of trip {trip_id!r}",
                    choices={"status": get_args(BookingStatus)},
                    dates=("book_by", "cancel_by"),
                )
                for i, b in enumerate(data.get("bookings") or [])
            ],
        )


@dataclass
class Ledger:
    schema_version: str = SCHEMA_VERSION
    profile: FamilyProfile | None = None
    trips: list[Trip] = field(default_factory=list)

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "Ledger":
        data = migrate(data)
        return _build(
            Ledger,
            data,
            "The ledger",
            profile=FamilyProfile.from_dict(data["profile"]) if data.get("profile") else None,
            trips=[Trip.from_dict(t) for t in data.get("trips") or []],
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def get(self, trip_id: str) -> Trip | None:
        return next((t for t in self.trips if t.id == trip_id), None)
