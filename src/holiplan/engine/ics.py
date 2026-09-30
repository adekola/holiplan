"""Minimal .ics export: all-day events for trips, plus VTODOs for deadlines."""

from __future__ import annotations

import hashlib
from datetime import date, datetime, timedelta, timezone

from .models import Ledger

PRODID = "-//holiplan//family holiday planner//EN"


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _esc(text: str) -> str:
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _fold(line: str) -> str:
    """RFC 5545: lines of at most 75 octets, continuation lines start with a space.

    Counted in UTF-8 bytes, never splitting a character, so umlauts stay intact.
    """
    lines: list[str] = []
    current, size = "", 0
    for char in line:
        width = len(char.encode("utf-8"))
        if size + width > 75:
            lines.append(current)
            current, size = " ", 1
        current += char
        size += width
    lines.append(current)
    return "\r\n".join(lines)


def _deadline_uid(item: dict) -> str:
    """Stable across exports, so re-importing updates a VTODO instead of duplicating it."""
    key = f"{item['kind']}|{item['trip_id']}|{item['message']}".encode("utf-8")
    return f"deadline-{hashlib.sha1(key).hexdigest()[:16]}@holiplan"


def to_ics(ledger: Ledger, include_deadlines: bool = True) -> str:
    from .checks import deadlines as compute_deadlines

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{PRODID}",
        "CALSCALE:GREGORIAN",
    ]

    for trip in ledger.trips:
        if trip.status == "cancelled":
            continue
        # DTEND is exclusive for all-day events
        end_exclusive = (date.fromisoformat(trip.end) + timedelta(days=1)).strftime("%Y%m%d")
        summary = trip.label if not trip.destination else f"{trip.label} ({trip.destination})"
        lines += [
            "BEGIN:VEVENT",
            f"UID:{trip.id}@holiplan",
            f"DTSTAMP:{_stamp()}",
            f"DTSTART;VALUE=DATE:{date.fromisoformat(trip.start).strftime('%Y%m%d')}",
            f"DTEND;VALUE=DATE:{end_exclusive}",
            _fold(f"SUMMARY:{_esc(summary)}"),
            f"STATUS:{'CONFIRMED' if trip.status == 'booked' else 'TENTATIVE'}",
            "TRANSP:TRANSPARENT" if trip.intent == "home" else "TRANSP:OPAQUE",
        ]
        if trip.notes:
            lines.append(_fold(f"DESCRIPTION:{_esc(trip.notes)}"))
        if trip.destination:
            lines.append(_fold(f"LOCATION:{_esc(trip.destination)}"))
        lines.append("END:VEVENT")

    if include_deadlines:
        for item in compute_deadlines(ledger):
            if item["kind"] == "unbooked":
                continue
            lines += [
                "BEGIN:VTODO",
                f"UID:{_deadline_uid(item)}",
                f"DTSTAMP:{_stamp()}",
                f"DUE;VALUE=DATE:{date.fromisoformat(item['due']).strftime('%Y%m%d')}",
                _fold(f"SUMMARY:{_esc(item['message'])}"),
                "END:VTODO",
            ]

    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"
