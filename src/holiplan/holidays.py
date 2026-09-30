"""OpenHolidays API client.

Data: https://openholidaysapi.org -- public and school holidays, no API key.
Licence: CC BY 4.0. Attribution is required wherever this data is shown.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

BASE = "https://openholidaysapi.org"
_CACHE: dict[tuple, tuple[float, Any]] = {}
_TTL = 60 * 60 * 24 * 7  # a week; holiday data changes rarely

log = logging.getLogger(__name__)


class HolidayDataUnavailable(RuntimeError):
    """OpenHolidays could not be reached and nothing usable is cached."""


def _get(path: str, params: dict[str, str]) -> Any:
    """GET from OpenHolidays, cached for a week.

    If the API fails, an expired cache entry is still served: holiday dates
    rarely change, and week-old data beats no answer. With nothing cached the
    caller gets a plain-language error instead of a raw HTTP one, so the
    assistant can say what went wrong rather than guess.
    """
    key = (path, tuple(sorted(params.items())))
    hit = _CACHE.get(key)
    now = time.time()
    if hit and now - hit[0] < _TTL:
        return hit[1]

    try:
        with httpx.Client(timeout=15) as client:
            response = client.get(
                f"{BASE}{path}", params=params, headers={"accept": "application/json"}
            )
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code < 500:
            raise ValueError(
                f"OpenHolidays rejected the request for {path.lstrip('/')} "
                f"({', '.join(f'{k}={v}' for k, v in params.items())}): "
                f"HTTP {exc.response.status_code}. Check the country and region codes."
            ) from exc
        return _stale_or_raise(path, hit, exc)
    except (httpx.HTTPError, ValueError) as exc:  # network failure or a non-JSON body
        return _stale_or_raise(path, hit, exc)

    _CACHE[key] = (now, data)
    return data


def _stale_or_raise(path: str, hit: tuple[float, Any] | None, exc: Exception) -> Any:
    if hit:
        log.warning("OpenHolidays unavailable (%s); serving cached %s", exc, path)
        return hit[1]
    raise HolidayDataUnavailable(
        "OpenHolidays (openholidaysapi.org) is not responding, so holiday dates "
        "can't be checked right now. Nothing has been calculated; try again in a "
        f"few minutes. ({type(exc).__name__}: {exc})"
    ) from exc


def public_holidays(
    country: str, start: str, end: str, region: str | None = None, language: str = "EN"
) -> list[dict]:
    params = {
        "countryIsoCode": country,
        "validFrom": start,
        "validTo": end,
        "languageIsoCode": language,
    }
    if region:
        params["subdivisionCode"] = region
    return _get("/PublicHolidays", params)


def school_holidays(
    country: str, start: str, end: str, region: str | None = None, language: str = "EN"
) -> list[dict]:
    params = {
        "countryIsoCode": country,
        "validFrom": start,
        "validTo": end,
        "languageIsoCode": language,
    }
    if region:
        params["subdivisionCode"] = region
    return _get("/SchoolHolidays", params)


def subdivisions(country: str) -> list[dict]:
    """The country's subdivision tree, names in every language the data has."""
    return _get("/Subdivisions", {"countryIsoCode": country})


def resolve_region(country: str, region: str | None) -> str | None:
    """The OpenHolidays code for `region`, which may also be given as its ISO code.

    OpenHolidays uses its own codes where they differ from ISO 3166-2: Vienna is
    "AT-WI", not "AT-9". It does not reject a code it doesn't know -- it just
    returns the nationwide holidays -- so an unknown region is an error here
    rather than a silently incomplete year.
    """
    if region is None:
        return None
    wanted = region.strip().casefold()
    tree = subdivisions(country)

    def walk(nodes: list[dict]) -> str | None:
        for node in nodes:
            if wanted in (node["code"].casefold(), (node.get("isoCode") or "").casefold()):
                return node["code"]
            found = walk(node.get("children", []))
            if found:
                return found
        return None

    code = walk(tree)
    if code is None:
        known = ", ".join(
            f"{n['code']} ({(n.get('name') or [{}])[0].get('text', '')})" for n in tree
        )
        raise ValueError(
            f"OpenHolidays has no region {region!r} in {country}. "
            f"Use one of: {known or 'none -- leave the region out'}."
        )
    return code


def covers(entry: dict, code: str) -> bool:
    """Whether a holiday applies throughout subdivision `code`.

    True when it is nationwide or lists `code` or one of its parents: Epiphany
    lists DE-BY, so it covers DE-BY and Augsburg (DE-BY-AU) alike. Entries
    without the flag are taken to apply everywhere.
    """
    if entry.get("nationwide", True):
        return True
    codes = [s.get("code", "") for s in entry.get("subdivisions", [])]
    return any(code == c or code.startswith(c + "-") for c in codes)


def is_half_day(entry: dict) -> bool:
    return entry.get("temporalScope") == "HalfDay"


def is_local(entry: dict, region: str | None) -> bool:
    """Whether a holiday applies to only part of `region`.

    `nationwide` alone can't tell: Epiphany is nationwide=false but covers all of
    DE-BY. Only a holiday that lists smaller areas than the region, like the
    Augsburg Peace Festival (DE-BY-AU), is local.
    """
    if region is None:
        return not entry.get("nationwide", True)
    return not covers(entry, region)


def _names(node: dict) -> set[str]:
    out = {node["code"].casefold()}
    for name in node.get("name", []):
        text = name.get("text", "").casefold()
        out.add(text)
        out.add(text.split(" (")[0])  # "Lengnau (AG)" also matches "Lengnau"
    return out


def locate(home: str, tree: list[dict], region: str | None = None) -> str | None:
    """The code of the most specific subdivision `home` names, inside `region`.

    `home` is free text such as "Augsburg" or "Augsburg, Germany"; any
    comma-separated part may match a subdivision's name (in any language) or
    code. Only subdivisions strictly below `region` count -- "Bavaria" says
    nothing about which town you live in. Returns None when nothing matches.
    """
    parts = {p.strip().casefold() for p in home.split(",") if p.strip()}
    best: str | None = None

    def walk(nodes: list[dict]) -> None:
        nonlocal best
        for node in nodes:
            code = node["code"]
            inside = region is None or code.startswith(region + "-")
            if inside and parts & _names(node) and (best is None or len(code) > len(best)):
                best = code
            walk(node.get("children", []))

    walk(tree)
    return best


def holiday_dates(
    entries: list[dict],
    region: str | None = None,
    local: bool | str = True,
    ignore: list[str] | tuple[str, ...] = (),
    half_days: bool = False,
) -> set[str]:
    """Flatten API entries into a set of ISO dates, expanding multi-day ranges.

    Only full-day holidays by default; `half_days=True` returns only those the
    data marks as half days instead (Zürich's Sechseläuten afternoon).

    `local` decides holidays that cover only part of `region` (see `is_local`):
    True keeps them all, False drops them all, and a subdivision code such as
    "DE-BY-AU" keeps only those that cover it. `ignore` drops any date whose
    "MM-DD" is listed.
    """
    from datetime import date, timedelta

    out: set[str] = set()
    for entry in entries:
        if is_half_day(entry) != half_days:
            continue
        if local is not True and is_local(entry, region):
            if local is False or not covers(entry, local):
                continue
        start = date.fromisoformat(entry["startDate"])
        end = date.fromisoformat(entry.get("endDate", entry["startDate"]))
        day = start
        while day <= end:
            if day.strftime("%m-%d") not in ignore:
                out.add(day.isoformat())
            day += timedelta(days=1)
    return out


def name_of(entries: list[dict], iso_date: str) -> str | None:
    for entry in entries:
        if entry["startDate"] <= iso_date <= entry.get("endDate", entry["startDate"]):
            names = entry.get("name", [])
            if names:
                return names[0].get("text")
    return None
