"""Server tests: the MCP tools with the OpenHolidays client stubbed out.

The engine is covered by test_engine.py; these check that the tools fetch the
right holidays and pass them through. They need the `mcp` package installed
and are skipped otherwise. No network.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_engine import DE_BY_2027, DE_BY_2027_WINDOWS, LEDGER  # noqa: E402
from test_holidays import AT_TREE, DE_BY_2028_API, DE_TREE  # noqa: E402

from holiplan import holidays  # noqa: E402

try:
    from mcp.server.mcpserver.exceptions import ToolError

    from holiplan import server  # noqa: E402
except ImportError:  # mcp not installed
    server = None


def _entries(dates: set[str]) -> list[dict]:
    return [{"startDate": d, "endDate": d, "name": [{"text": "Holiday"}]} for d in sorted(dates)]


def fake_public(country, start, end, region=None, language="EN"):
    entries = _entries(DE_BY_2027) + DE_BY_2028_API
    return [e for e in entries if start <= e["startDate"] <= end]


def fake_school(country, start, end, region=None, language="EN"):
    return [
        {"startDate": w["start"], "endDate": w["end"], "name": [{"text": w["name"]}]}
        for w in DE_BY_2027_WINDOWS
        if start <= w["start"] <= end
    ]


class StubbedHolidays(unittest.TestCase):
    """Replaces the three OpenHolidays calls with the fixtures above."""

    def setUp(self):
        patches = [
            mock.patch("holiplan.holidays.public_holidays", side_effect=fake_public),
            mock.patch("holiplan.holidays.school_holidays", side_effect=fake_school),
            mock.patch("holiplan.holidays.subdivisions", return_value=DE_TREE),
        ]
        self.public, self.school, self.subdivisions = (p.start() for p in patches)
        for p in patches:
            self.addCleanup(p.stop)


@unittest.skipIf(server is None, "mcp is not installed")
class ServerTests(StubbedHolidays):

    def test_clean_ledger_passes(self):
        result = server.check_ledger(LEDGER)
        self.assertEqual(result["checked_trips"], 4)
        self.assertEqual(result["issues"], [])

    def test_holidays_are_fetched_for_whole_years_and_the_edges(self):
        server.check_ledger(LEDGER)
        # Whole years, plus a month either side for windows that cross New Year.
        self.assertEqual(self.public.call_args.args[1:3], ("2026-12-01", "2028-01-31"))
        self.assertEqual(self.school.call_args.args[1:3], ("2027-01-01", "2027-12-31"))

    def test_trip_outside_school_holidays_is_flagged(self):
        june = {"id": "june", "label": "June weekend", "start": "2027-06-10", "end": "2027-06-14"}
        result = server.check_ledger({**LEDGER, "trips": LEDGER["trips"] + [june]})
        flagged = [i for i in result["issues"] if i["code"] == "outside_school_window"]
        self.assertEqual([i["trip_ids"] for i in flagged], [["june"]])

    def test_overlaps_are_caught_without_a_profile(self):
        clash = {"id": "clash", "label": "Clash", "start": "2027-05-20", "end": "2027-05-25"}
        ledger = {"schema_version": "1", "trips": LEDGER["trips"] + [clash]}
        result = server.check_ledger(ledger)
        self.assertIn("overlap", {i["code"] for i in result["issues"]})
        self.public.assert_not_called()

    def test_upsert_replaces_by_id_and_returns_checks(self):
        moved = {**LEDGER["trips"][2], "start": "2027-06-10", "end": "2027-06-14"}
        result = server.upsert_trip(LEDGER, moved)
        trips = result["ledger"]["trips"]
        self.assertEqual(len(trips), 4)
        self.assertEqual(next(t for t in trips if t["id"] == "pentecost")["start"], "2027-06-10")
        self.assertIn("outside_school_window", {i["code"] for i in result["issues"]})

    def test_region_is_accepted_as_an_iso_code(self):
        self.subdivisions.return_value = AT_TREE
        server.calculate_leave_cost("2027-03-22", "2027-03-26", "AT", "AT-9")
        self.assertEqual(self.public.call_args.args[3], "AT-WI")

    def test_unknown_region_is_an_error_not_a_quiet_empty_year(self):
        with self.assertRaisesRegex(ToolError, "DE-BY"):
            server.get_holiday_windows("DE", 2027, "DE-XX")

    def test_deliberate_errors_are_tool_errors_so_the_assistant_sees_them(self):
        # The SDK hides the text of any other exception from the client.
        with self.assertRaisesRegex(ToolError, "Trip 'x' is missing end"):
            server.check_ledger({"schema_version": "1", "trips": [{"id": "x", "label": "x", "start": "2027-01-01"}]})
        self.public.side_effect = holidays.HolidayDataUnavailable("OpenHolidays is not responding")
        with self.assertRaisesRegex(ToolError, "not responding"):
            server.calculate_leave_cost("2027-03-22", "2027-03-26", "DE", "DE-BY")

    def test_names_come_in_the_requested_language(self):
        result = server.get_holiday_windows("DE", 2027, "DE-BY", language="de")
        self.assertEqual(result["language"], "DE")
        self.assertEqual(self.public.call_args.args[4], "DE")
        self.assertEqual(self.school.call_args.args[4], "DE")
        server.summarise_plan(LEDGER, today="2027-01-15", language="DE")
        self.assertEqual(self.school.call_args.args[4], "DE")

    def test_bad_language_is_explained(self):
        with self.assertRaisesRegex(ToolError, "two-letter code"):
            server.get_holiday_windows("DE", 2027, "DE-BY", language="German")

    def test_holiday_windows_are_shaped_for_the_assistant(self):
        result = server.get_holiday_windows("DE", 2027, "DE-BY")
        self.assertEqual(
            result["school_windows"][0],
            {
                "name": "Spring Holidays",
                "start": "2027-02-08",
                "end": "2027-02-12",
                "effective_start": "2027-02-06",
                "effective_end": "2027-02-14",
                "days_off": 9,
                "school_days": 5,
                "closure": False,
                "groups": [],
            },
        )
        self.assertEqual(result["source"], "OpenHolidays (CC BY 4.0)")

    def test_holiday_windows_give_effective_dates(self):
        windows = server.get_holiday_windows("DE", 2027, "DE-BY")["school_windows"]
        effective = {w["name"]: (w["effective_start"], w["effective_end"]) for w in windows}
        self.assertEqual(effective["Easter Holidays"], ("2027-03-20", "2027-04-04"))
        # Whit Monday (17 May) sits just before the official window.
        self.assertEqual(effective["Pentecost Holidays"], ("2027-05-15", "2027-05-30"))

    def test_public_holidays_are_listed_for_the_year_only(self):
        dates = [h["date"] for h in server.get_holiday_windows("DE", 2027, "DE-BY")["public_holidays"]]
        self.assertTrue(all(d.startswith("2027-") for d in dates))

    def test_repeats_per_school_type_are_merged_and_closures_split_out(self):
        def entry(name, start, end, *groups):
            return {
                "name": [{"text": name}],
                "startDate": start,
                "endDate": end,
                "groups": [{"code": g} for g in groups],
            }

        self.school.side_effect = None
        self.school.return_value = [
            entry("Spring holidays", "2027-04-26", "2027-05-08", "CH-ZH-BS", "CH-ZH-MS"),
            entry("Spring holidays", "2027-04-26", "2027-05-08", "CH-ZH-VS"),
            entry("Ascension Bridge", "2027-05-06", "2027-05-08", "CH-ZH-BS", "CH-ZH-MS"),
            entry("Christmas holidays", "2027-12-20", "2028-01-01"),
        ]
        result = server.get_holiday_windows("DE", 2027, "DE-BY")
        windows = {w["name"]: w for w in result["school_windows"]}
        self.assertEqual(windows["Spring holidays"]["groups"], ["CH-ZH-BS", "CH-ZH-MS", "CH-ZH-VS"])
        self.assertEqual(windows["Christmas holidays"]["groups"], [])
        self.assertEqual([c["name"] for c in result["school_closures"]], ["Ascension Bridge"])


# Two weeks spanning both of Bavaria's partial holidays: ten workdays.
AUGUST_2028 = {"start": "2028-08-07", "end": "2028-08-18", "country": "DE", "region": "DE-BY"}


def ledger_2028(**profile_settings) -> dict:
    profile = {**LEDGER["profile"], "leave": [{"year": 2028, "allowance": 30}], **profile_settings}
    trip = {"id": "aug", "label": "August lakes", "start": AUGUST_2028["start"], "end": AUGUST_2028["end"]}
    return {"schema_version": "1", "profile": profile, "trips": [trip]}


@unittest.skipIf(server is None, "mcp is not installed")
class LocalHolidayTests(StubbedHolidays):
    """The August fortnight costs 8 days if both partial holidays are off, 10 if neither."""

    def cost(self, **profile_settings) -> float:
        ledger = ledger_2028(**profile_settings)
        return server.calculate_leave_cost(**AUGUST_2028, ledger=ledger)["leave_days"]

    def test_home_town_keeps_its_own_local_holiday(self):
        self.assertEqual(self.cost(home="Augsburg"), 8)

    def test_home_town_without_local_holidays_pays_for_them(self):
        self.assertEqual(self.cost(home="Munich"), 9)

    def test_unknown_home_keeps_every_local_holiday(self):
        self.assertEqual(self.cost(home=None), 8)
        self.assertEqual(self.cost(home="48.37,10.89"), 8)
        self.assertEqual(server.calculate_leave_cost(**AUGUST_2028)["leave_days"], 8)

    def test_explicit_setting_overrides_home(self):
        self.assertEqual(self.cost(home="Munich", local_holidays="include"), 8)
        self.assertEqual(self.cost(home="Augsburg", local_holidays="exclude"), 9)

    def test_ignored_holiday_costs_leave(self):
        self.assertEqual(self.cost(home="Augsburg", ignore_holidays=["08-15"]), 9)
        self.assertEqual(self.cost(home="Munich", ignore_holidays=["08-15"]), 10)

    def test_year_budget_follows_home(self):
        self.assertEqual(server.get_year_budget(ledger_2028(home="Munich"), 2028)["planned"], 9)
        self.assertEqual(server.get_year_budget(ledger_2028(home="Augsburg"), 2028)["planned"], 8)

    def observed(self, home: str | None) -> dict[str, bool | None]:
        result = server.get_holiday_windows("DE", 2028, "DE-BY", home=home)
        return {h["date"]: h["observed"] for h in result["public_holidays"]}

    def test_holiday_windows_say_whether_home_observes_each_holiday(self):
        self.assertTrue(self.observed("Augsburg")["2028-08-08"])
        self.assertFalse(self.observed("Munich")["2028-08-08"])
        self.assertIsNone(self.observed(None)["2028-08-08"])
        for home in ("Augsburg", "Munich", None):
            self.assertTrue(self.observed(home)["2028-08-15"])

    def test_holiday_windows_mark_local_holidays(self):
        result = server.get_holiday_windows("DE", 2028, "DE-BY")
        local = {h["date"]: h["local"] for h in result["public_holidays"]}
        self.assertTrue(local["2028-08-08"])
        self.assertFalse(local["2028-08-15"])
        self.assertFalse(local["2028-01-06"])


if __name__ == "__main__":
    unittest.main()
