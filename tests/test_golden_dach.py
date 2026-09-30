"""Golden tests for Vienna (AT-WI) and Zürich (CH-ZH), 2027.

The fixtures in tests/fixtures are real OpenHolidays responses (CC BY 4.0),
recorded on 2026-09-24 and replayed offline through the MCP tools. Every
expected figure below was worked out by hand from the calendar, not from the
code, so a failure means the arithmetic a family relies on broke -- or the
recorded data was re-recorded and changed.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

try:
    from holiplan import server  # noqa: E402
except ImportError:  # mcp not installed
    server = None

FIXTURES = Path(__file__).resolve().parent / "fixtures"


class Recorded(unittest.TestCase):
    """Serves one recorded region through the three OpenHolidays calls."""

    fixture: str

    def setUp(self):
        data = json.loads((FIXTURES / self.fixture).read_text(encoding="utf-8"))

        def public(country, start, end, region=None, language="EN"):
            return [e for e in data["public"] if start <= e["startDate"] <= end]

        def school(country, start, end, region=None, language="EN"):
            return [e for e in data["school"] if e["startDate"] <= end and e["endDate"] >= start]

        patches = [
            mock.patch("holiplan.holidays.public_holidays", side_effect=public),
            mock.patch("holiplan.holidays.school_holidays", side_effect=school),
            mock.patch("holiplan.holidays.subdivisions", return_value=data["subdivisions"]),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)


def trip(id: str, start: str, end: str, **extra) -> dict:
    return {"id": id, "label": id, "start": start, "end": end, **extra}


# A Vienna family. The region is given as its ISO code on purpose: OpenHolidays
# calls Vienna AT-WI, and AT-9 used to return only the nationwide holidays.
VIENNA = {
    "schema_version": "1",
    "profile": {
        "country": "AT",
        "region": "AT-9",
        "home": "Wien",
        "leave": [{"year": 2027, "allowance": 25}],
    },
    "trips": [
        # Semester holidays, Sat 30 Jan - Sat 6 Feb: Mon 1 - Fri 5 Feb is leave.
        trip("semester", "2027-01-30", "2027-02-06"),
        # Easter, Sat 20 - Mon 29 Mar. Good Friday is a workday in Austria, so
        # 22-26 Mar is all leave (Bavaria pays 4); Easter Monday is a holiday.
        trip("easter", "2027-03-20", "2027-03-29"),
        # Summer, Sat 3 - Sat 17 Jul: two full weeks.
        trip("summer", "2027-07-03", "2027-07-17"),
        # Autumn, Tue 26 - Sun 31 Oct: National Day on the 26th, then 27-29 Oct.
        trip("autumn", "2027-10-26", "2027-10-31"),
    ],
}


@unittest.skipIf(server is None, "mcp is not installed")
class ViennaTests(Recorded):
    fixture = "at-wi-2027.json"

    def cost(self, start: str, end: str) -> float:
        return server.calculate_leave_cost(start, end, "AT", "AT-9", ledger=VIENNA)["leave_days"]

    def test_each_trip_costs_what_the_calendar_says(self):
        self.assertEqual(self.cost("2027-01-30", "2027-02-06"), 5)
        self.assertEqual(self.cost("2027-03-20", "2027-03-29"), 5)
        self.assertEqual(self.cost("2027-07-03", "2027-07-17"), 10)
        self.assertEqual(self.cost("2027-10-26", "2027-10-31"), 3)

    def test_year_budget(self):
        budget = server.get_year_budget(VIENNA, 2027)
        self.assertEqual((budget["planned"], budget["remaining"]), (23, 2))

    def test_plan_checks_clean(self):
        self.assertEqual(server.check_ledger(VIENNA)["issues"], [])

    def test_trip_starting_on_a_school_day_is_flagged(self):
        # Mon 25 Oct is a school day: the autumn window only stretches back to
        # National Day on the 26th, not to the weekend before it.
        early = {**VIENNA, "trips": [trip("autumn", "2027-10-23", "2027-10-31")]}
        issues = server.check_ledger(early)["issues"]
        self.assertEqual([i["code"] for i in issues], ["outside_school_window"])

    def test_windows_include_the_vienna_only_ones(self):
        result = server.get_holiday_windows("AT", 2027, "AT-9")
        self.assertEqual(result["region"], "AT-WI")
        names = [w["name"] for w in result["school_windows"]]
        self.assertEqual(
            names,
            [
                "Christmas Holidays",
                "Semester Holidays",
                "Easter Holidays",
                "Summer Holidays",
                "Autumn Holidays",
                "Christmas Holidays",
            ],
        )

    def test_effective_windows(self):
        windows = {
            w["name"]: (w["effective_start"], w["effective_end"], w["days_off"])
            for w in server.get_holiday_windows("AT", 2027, "AT-WI")["school_windows"]
        }
        # Sat 30 Jan - Sat 6 Feb, plus Sunday 7 Feb.
        self.assertEqual(windows["Semester Holidays"], ("2027-01-30", "2027-02-07", 9))
        # Wed 27 - Sun 31 Oct, plus National Day before and All Saints' after.
        self.assertEqual(windows["Autumn Holidays"], ("2027-10-26", "2027-11-01", 7))

    def test_bridge_days(self):
        result = server.find_bridge_days("AT", 2027, "AT-9", ledger=VIENNA, limit=4)
        self.assertEqual(
            [(o["start"], o["end"], o["days_off_per_leave_day"], o["school"]) for o in result["options"]],
            [
                ("2027-05-06", "2027-05-09", 4.0, "term"),  # Friday after Ascension
                ("2027-05-27", "2027-05-30", 4.0, "term"),  # Friday after Corpus Christi
                # Monday before National Day; autumn holidays start on the Tuesday.
                ("2027-10-23", "2027-10-26", 4.0, "partly"),
                # 4-5 Jan up to Epiphany, inside the Christmas holidays.
                ("2027-01-01", "2027-01-06", 3.0, "holiday"),
            ],
        )
        self.assertGreater(result["more"], 0)

    def test_summary(self):
        result = server.summarise_plan(VIENNA, today="2027-01-15")
        self.assertEqual(result["years"][0]["planned"], 23)
        self.assertEqual([t["leave_days"] for t in result["trips"]], [5, 5, 10, 3])
        # Every 2027 window has a trip except Christmas, which runs 24 Dec to
        # Epiphany; 7 Jan 2028 is a Friday and a workday.
        self.assertEqual(
            result["open_windows"],
            [
                {
                    "name": "Christmas Holidays",
                    "effective_start": "2027-12-24",
                    "effective_end": "2028-01-06",
                    "days_off": 14,
                }
            ],
        )

    def test_closures_are_listed_apart(self):
        closures = server.get_holiday_windows("AT", 2027, "AT-WI")["school_closures"]
        self.assertEqual(
            [(c["name"], c["school_days"]) for c in closures],
            [("Pentecost Holidays", 0), ("All Souls' Day", 1), ("Saint Leopold's Day", 1)],
        )


# A family in the city of Zürich.
ZURICH = {
    "schema_version": "1",
    "profile": {
        "country": "CH",
        "region": "CH-ZH",
        "home": "Zürich",
        "leave": [{"year": 2027, "allowance": 25}],
    },
    "trips": [
        # Spring holidays, Sat 24 Apr - Sun 9 May: 26-30 Apr and 3-7 May less
        # Ascension (Thu 6 May). Labour Day falls on a Saturday.
        trip("spring", "2027-04-24", "2027-05-09"),
        # Summer, Sat 17 - Sat 31 Jul: two full weeks; 1 August is a Sunday.
        trip("summer", "2027-07-17", "2027-07-31"),
        # Autumn, Sat 9 - Sun 17 Oct: one full week.
        trip("autumn", "2027-10-09", "2027-10-17"),
    ],
}


@unittest.skipIf(server is None, "mcp is not installed")
class ZurichTests(Recorded):
    fixture = "ch-zh-2027.json"

    def cost(self, start: str, end: str, home: str | None = "Zürich") -> float:
        ledger = {**ZURICH, "profile": {**ZURICH["profile"], "home": home}}
        return server.calculate_leave_cost(start, end, "CH", "CH-ZH", ledger=ledger)["leave_days"]

    def test_each_trip_costs_what_the_calendar_says(self):
        self.assertEqual(self.cost("2027-04-24", "2027-05-09"), 9)
        self.assertEqual(self.cost("2027-07-17", "2027-07-31"), 10)
        self.assertEqual(self.cost("2027-10-09", "2027-10-17"), 5)

    def test_year_budget(self):
        budget = server.get_year_budget(ZURICH, 2027)
        self.assertEqual((budget["planned"], budget["remaining"]), (24, 1))

    def test_plan_checks_clean(self):
        self.assertEqual(server.check_ledger(ZURICH)["issues"], [])

    def test_sechselaeuten_is_a_half_day_in_the_city_only(self):
        # Mon 19 Apr: afternoon off in the city of Zürich, a full workday in Winterthur.
        self.assertEqual(self.cost("2027-04-19", "2027-04-23"), 4.5)
        self.assertEqual(self.cost("2027-04-19", "2027-04-23", home="Winterthur"), 5)
        # Without a home, every local holiday is kept.
        self.assertEqual(self.cost("2027-04-19", "2027-04-23", home=None), 4.5)

    def test_knabenschiessen_is_a_half_day_too(self):
        self.assertEqual(self.cost("2027-09-13", "2027-09-17"), 4.5)

    def test_windows_merge_school_types_and_split_out_closures(self):
        result = server.get_holiday_windows("CH", 2027, "CH-ZH", home="Zürich")
        windows = {w["name"]: w for w in result["school_windows"]}
        self.assertEqual(windows["Spring holidays"]["groups"], ["CH-ZH-BS", "CH-ZH-MS", "CH-ZH-VS"])
        self.assertEqual(
            (windows["Spring holidays"]["effective_start"], windows["Spring holidays"]["effective_end"]),
            ("2027-04-24", "2027-05-09"),
        )
        # Only primary schools have a summer entry in the data.
        self.assertEqual(windows["Summer holidays"]["groups"], ["CH-ZH-VS"])
        closures = {c["name"]: c for c in result["school_closures"]}
        # Easter is Good Friday to Easter Monday: no school days at all.
        self.assertEqual(closures["Easter"]["school_days"], 0)
        # The Ascension bridge (Fri 7 May) is for secondary and vocational schools only.
        self.assertEqual(closures["Ascension Bridge"]["groups"], ["CH-ZH-BS", "CH-ZH-MS"])

    def test_half_day_local_holidays_are_marked(self):
        holidays = {
            h["name"]: h
            for h in server.get_holiday_windows("CH", 2027, "CH-ZH", home="Winterthur")[
                "public_holidays"
            ]
        }
        sechselaeuten = holidays["Sechseläuten"]
        self.assertEqual(
            (sechselaeuten["half_day"], sechselaeuten["local"], sechselaeuten["observed"]),
            (True, True, False),
        )


if __name__ == "__main__":
    unittest.main()
