"""Local-holiday filtering in the OpenHolidays client. No network.

The fixture mirrors what OpenHolidays returns for DE-BY in 2028, a year in
which both of Bavaria's partial holidays -- the Augsburg Peace Festival (8 Aug)
and Assumption Day (15 Aug) -- fall on a Tuesday.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from unittest import mock  # noqa: E402

import httpx  # noqa: E402

from holiplan import holidays  # noqa: E402
from holiplan.holidays import covers, holiday_dates, is_local, locate, resolve_region  # noqa: E402


def _entry(day: str, name: str, nationwide: bool, subdivisions: list[str] = ()) -> dict:
    return {
        "startDate": day,
        "endDate": day,
        "name": [{"language": "EN", "text": name}],
        "nationwide": nationwide,
        "subdivisions": [{"code": code} for code in subdivisions],
    }


DE_BY_2028_API = [
    _entry("2028-01-06", "Epiphany", False, ["DE-ST", "DE-BW", "DE-BY"]),
    _entry("2028-06-05", "Pentecost Monday", True),
    _entry("2028-06-15", "Corpus Christi", False, ["DE-HE", "DE-SL", "DE-NW", "DE-BW", "DE-RP", "DE-BY"]),
    _entry("2028-08-08", "High Festival of Peace", False, ["DE-BY-AU"]),
    _entry("2028-08-15", "Assumption Day", False, ["DE-SL", "DE-BY"]),
    _entry("2028-10-03", "Day of German Unity", True),
]


def _node(
    code: str, names: dict[str, str], children: list[dict] = (), iso: str | None = None
) -> dict:
    return {
        "code": code,
        "isoCode": iso,
        "name": [{"language": lang, "text": text} for lang, text in names.items()],
        "children": list(children),
    }


# Shaped like /Subdivisions: names in every language, municipalities as children.
DE_TREE = [
    _node("DE-BW", {"DE": "Baden-Württemberg", "EN": "Baden-Württemberg"}),
    _node(
        "DE-BY",
        {"DE": "Bayern", "EN": "Bavaria"},
        [_node("DE-BY-AU", {"DE": "Augsburg", "EN": "Augsburg"})],
    ),
]
# OpenHolidays' own codes differ from ISO 3166-2 in Austria.
AT_TREE = [
    _node("AT-NÖ", {"DE": "Niederösterreich", "EN": "Lower Austria"}, iso="AT-3"),
    _node("AT-WI", {"DE": "Wien", "EN": "Vienna"}, iso="AT-9"),
]
CH_TREE = [
    _node(
        "CH-AG",
        {"DE": "Aargau"},
        [_node("CH-AG-ZZ", {"DE": "Zurzach"}, [_node("CH-AG-ZZ-LN", {"DE": "Lengnau (AG)"})])],
    )
]


def _by_name(name: str) -> dict:
    return next(e for e in DE_BY_2028_API if e["name"][0]["text"] == name)


class IsLocalTests(unittest.TestCase):
    def test_nationwide_holiday_is_not_local(self):
        self.assertFalse(is_local(_by_name("Day of German Unity"), "DE-BY"))

    def test_statewide_holiday_is_not_local_despite_the_flag(self):
        # nationwide=false, but it covers all of Bavaria.
        self.assertFalse(is_local(_by_name("Epiphany"), "DE-BY"))
        self.assertFalse(is_local(_by_name("Corpus Christi"), "DE-BY"))

    def test_city_holiday_is_local(self):
        self.assertTrue(is_local(_by_name("High Festival of Peace"), "DE-BY"))

    def test_holiday_for_a_parent_region_covers_a_child_region(self):
        # A profile set to Augsburg itself gets both the state and the city holidays.
        self.assertFalse(is_local(_by_name("Epiphany"), "DE-BY-AU"))
        self.assertFalse(is_local(_by_name("High Festival of Peace"), "DE-BY-AU"))

    def test_without_a_region_anything_not_nationwide_is_local(self):
        self.assertTrue(is_local(_by_name("Epiphany"), None))

    def test_entries_without_the_flag_apply_everywhere(self):
        self.assertFalse(is_local({"startDate": "2028-01-01"}, "DE-BY"))


class HolidayDatesTests(unittest.TestCase):
    def test_local_holidays_are_included_by_default(self):
        dates = holiday_dates(DE_BY_2028_API, "DE-BY")
        self.assertIn("2028-08-08", dates)
        self.assertIn("2028-08-15", dates)

    def test_excluding_local_holidays_keeps_statewide_ones(self):
        dates = holiday_dates(DE_BY_2028_API, "DE-BY", local=False)
        self.assertNotIn("2028-08-08", dates)
        for kept in ("2028-01-06", "2028-06-15", "2028-08-15", "2028-10-03"):
            self.assertIn(kept, dates)

    def test_a_locality_keeps_only_its_own_local_holidays(self):
        self.assertIn("2028-08-08", holiday_dates(DE_BY_2028_API, "DE-BY", local="DE-BY-AU"))
        self.assertNotIn("2028-08-08", holiday_dates(DE_BY_2028_API, "DE-BY", local="DE-BY-XX"))
        # Region-wide holidays are untouched either way.
        self.assertIn("2028-01-06", holiday_dates(DE_BY_2028_API, "DE-BY", local="DE-BY-XX"))

    def test_half_days_are_kept_apart_from_full_days(self):
        entries = DE_BY_2028_API + [
            {**_entry("2028-04-17", "Sechseläuten", False, ["DE-BY"]), "temporalScope": "HalfDay"}
        ]
        self.assertNotIn("2028-04-17", holiday_dates(entries, "DE-BY"))
        self.assertEqual(holiday_dates(entries, "DE-BY", half_days=True), {"2028-04-17"})

    def test_ignored_dates_are_dropped(self):
        dates = holiday_dates(DE_BY_2028_API, "DE-BY", ignore=["08-15"])
        self.assertNotIn("2028-08-15", dates)
        self.assertIn("2028-08-08", dates)


class CoversTests(unittest.TestCase):
    def test_statewide_holiday_covers_a_town_in_the_state(self):
        self.assertTrue(covers(_by_name("Epiphany"), "DE-BY-AU"))

    def test_town_holiday_covers_only_that_town(self):
        self.assertTrue(covers(_by_name("High Festival of Peace"), "DE-BY-AU"))
        self.assertFalse(covers(_by_name("High Festival of Peace"), "DE-BY"))


class LocateTests(unittest.TestCase):
    def test_town_name_is_found(self):
        self.assertEqual(locate("Augsburg", DE_TREE, "DE-BY"), "DE-BY-AU")

    def test_any_comma_separated_part_and_any_case_matches(self):
        self.assertEqual(locate("augsburg, Germany", DE_TREE, "DE-BY"), "DE-BY-AU")

    def test_town_without_local_holidays_is_not_found(self):
        self.assertIsNone(locate("Munich", DE_TREE, "DE-BY"))

    def test_the_region_itself_is_not_a_town(self):
        self.assertIsNone(locate("Bavaria", DE_TREE, "DE-BY"))

    def test_town_outside_the_region_is_ignored(self):
        self.assertIsNone(locate("Augsburg", DE_TREE, "DE-BW"))

    def test_disambiguated_names_match_without_the_suffix(self):
        self.assertEqual(locate("Lengnau", CH_TREE, "CH-AG"), "CH-AG-ZZ-LN")


class ResolveRegionTests(unittest.TestCase):
    def resolve(self, region: str | None, tree: list[dict] = AT_TREE) -> str | None:
        with mock.patch("holiplan.holidays.subdivisions", return_value=tree):
            return resolve_region("AT", region)

    def test_iso_code_becomes_the_openholidays_code(self):
        self.assertEqual(self.resolve("AT-9"), "AT-WI")

    def test_openholidays_code_and_any_case_are_accepted(self):
        self.assertEqual(self.resolve("AT-WI"), "AT-WI")
        self.assertEqual(self.resolve("at-wi"), "AT-WI")

    def test_nested_codes_resolve(self):
        self.assertEqual(self.resolve("CH-AG-ZZ-LN", CH_TREE), "CH-AG-ZZ-LN")

    def test_no_region_stays_none(self):
        self.assertIsNone(self.resolve(None))

    def test_unknown_region_lists_the_valid_ones(self):
        with self.assertRaises(ValueError) as caught:
            self.resolve("AT-10")
        self.assertIn("AT-WI (Wien)", str(caught.exception))


_REAL_CLIENT = httpx.Client


class FetchTests(unittest.TestCase):
    """The HTTP layer, driven through httpx's mock transport."""

    def setUp(self):
        saved = dict(holidays._CACHE)
        holidays._CACHE.clear()
        self.addCleanup(lambda: (holidays._CACHE.clear(), holidays._CACHE.update(saved)))

    def serve(self, handler) -> None:
        def client(**kwargs):
            return _REAL_CLIENT(transport=httpx.MockTransport(handler), **kwargs)

        patch = mock.patch("holiplan.holidays.httpx.Client", side_effect=client)
        patch.start()
        self.addCleanup(patch.stop)

    def fetch(self):
        return holidays.public_holidays("DE", "2028-01-01", "2028-12-31", "DE-BY")

    def test_answers_are_cached(self):
        calls = []
        self.serve(lambda request: calls.append(request) or httpx.Response(200, json=DE_BY_2028_API))
        self.assertEqual(self.fetch(), DE_BY_2028_API)
        self.assertEqual(self.fetch(), DE_BY_2028_API)
        self.assertEqual(len(calls), 1)

    def test_outage_without_cache_says_so_plainly(self):
        def down(request):
            raise httpx.ConnectError("connection refused", request=request)

        self.serve(down)
        with self.assertRaisesRegex(holidays.HolidayDataUnavailable, "not responding"):
            self.fetch()

    def test_server_error_without_cache_says_so_plainly(self):
        self.serve(lambda request: httpx.Response(503))
        with self.assertRaises(holidays.HolidayDataUnavailable):
            self.fetch()

    def test_outage_serves_expired_cache(self):
        self.serve(lambda request: httpx.Response(200, json=DE_BY_2028_API))
        self.fetch()
        for key, (stamp, data) in list(holidays._CACHE.items()):
            holidays._CACHE[key] = (stamp - holidays._TTL - 1, data)
        self.serve(lambda request: httpx.Response(503))
        with self.assertLogs("holiplan.holidays", "WARNING"):
            self.assertEqual(self.fetch(), DE_BY_2028_API)

    def test_bad_request_blames_the_input_not_the_service(self):
        self.serve(lambda request: httpx.Response(400))
        with self.assertRaisesRegex(ValueError, "Check the country and region"):
            self.fetch()

    def test_garbage_body_counts_as_an_outage(self):
        self.serve(lambda request: httpx.Response(200, text="<html>maintenance</html>"))
        with self.assertRaises(holidays.HolidayDataUnavailable):
            self.fetch()


if __name__ == "__main__":
    unittest.main()
