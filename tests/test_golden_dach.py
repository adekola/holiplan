"""Golden tests for Vienna (AT-WI), Zürich (CH-ZH), Mecklenburg-Vorpommern
(DE-MV), Schleswig-Holstein (DE-SH), Appenzell Innerrhoden (CH-AI) and
Graubünden (CH-GR), 2027.

The fixtures in tests/fixtures are real OpenHolidays responses (CC BY 4.0),
recorded on 2026-09-24 (Vienna, Zürich) and 2026-10-27 (the others) and
replayed offline through the MCP tools. The CH-AI and CH-GR subdivision trees
keep municipalities only for their own canton (and Appenzell Ausserrhoden, for
a home outside CH-AI), and their groups only that canton's. Every
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
    """Serves one recorded region through the four OpenHolidays calls."""

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
            mock.patch("holiplan.holidays.groups", return_value=data.get("groups", [])),
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

    def test_a_drafted_year(self):
        from holiplan import service

        empty = {**VIENNA, "trips": []}
        result = service.draft_year(empty, 2027)
        self.assertEqual(
            [(t["start"], t["end"], t["leave_days"]) for t in result["drafted"]],
            [
                # Semester holidays, Sat 30 Jan - Sun 7 Feb: Mon 1 - Fri 5 Feb.
                ("2027-01-30", "2027-02-07", 5),
                # Easter, Sat 20 - Sun 28 Mar: Good Friday is a workday in Austria, so 5.
                ("2027-03-20", "2027-03-28", 5),
                # Summer, two weeks from Sat 3 Jul: ten workdays.
                ("2027-07-03", "2027-07-18", 10),
                # Autumn, Tue 26 Oct - Mon 1 Nov, the whole holiday: National Day and All
                # Saints' Day are free, 27-29 Oct is leave.
                ("2027-10-26", "2027-11-01", 3),
            ],
        )
        # Whitsun is a closure here, not a holiday to travel in; Christmas crosses New Year.
        self.assertEqual(sum(t["leave_days"] for t in result["drafted"]), 23)
        self.assertEqual(result["issues"], [])
        self.assertEqual(len(result["ledger"]["trips"]), 4)

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
        self.assertEqual(result["school_groups"], ["CH-ZH-BS", "CH-ZH-MS", "CH-ZH-VS"])
        # Without a choice, the Volksschule's holidays.
        self.assertEqual(result["school_group"], "CH-ZH-VS")
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
        self.assertNotIn("Ascension Bridge", closures)

    def test_a_secondary_school_gets_the_ascension_bridge(self):
        result = server.get_holiday_windows("CH", 2027, "CH-ZH", school_group="CH-ZH-MS")
        self.assertEqual(result["school_group"], "CH-ZH-MS")
        closures = {c["name"]: c for c in result["school_closures"]}
        self.assertEqual(closures["Ascension Bridge"]["groups"], ["CH-ZH-BS", "CH-ZH-MS"])
        # The data has no summer holidays for Mittelschulen, so none are shown.
        self.assertNotIn("Summer holidays", {w["name"] for w in result["school_windows"]})

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


# A family in Rostock whose children go to a general school (the default).
ROSTOCK = {
    "schema_version": "1",
    "profile": {
        "country": "DE",
        "region": "DE-MV",
        "leave": [{"year": 2027, "allowance": 30}],
    },
    "trips": [
        # Winter holidays (general schools 8-19 Feb), Sat 6 - Sun 21 Feb: two
        # full weeks, no public holidays.
        trip("winter", "2027-02-06", "2027-02-21"),
        # Summer (general schools from Mon 5 Jul), Sat 3 - Sun 18 Jul: two weeks.
        trip("summer", "2027-07-03", "2027-07-18"),
    ],
}


@unittest.skipIf(server is None, "mcp is not installed")
class MecklenburgTests(Recorded):
    """General and vocational schools have different holidays in DE-MV."""

    fixture = "de-mv-2027.json"

    def with_group(self, group: str) -> dict:
        return {**ROSTOCK, "profile": {**ROSTOCK["profile"], "school_group": group}}

    def test_one_set_of_holidays_for_general_schools(self):
        result = server.get_holiday_windows("DE", 2027, "DE-MV")
        self.assertEqual(result["school_groups"], ["DE-MV-ABS", "DE-MV-BBS"])
        self.assertEqual(result["school_group"], "DE-MV-ABS")
        names = [w["name"] for w in result["school_windows"]]
        self.assertEqual(names.count("Winter Holidays"), 1)
        # Christmas twice, the one ending in January and the one starting in December.
        self.assertEqual(names.count("Christmas Holidays"), 2)
        self.assertEqual(len(names), len(set(names)) + 1)
        easter = next(w for w in result["school_windows"] if w["name"] == "Easter Holidays")
        # Wed 24 Mar - Fri 2 Apr; Tue 23 Mar and Mon 5 Apr are school days, so it
        # stretches only to Sun 4 Apr. Good Friday (26th) and Easter Monday (29th)
        # leave six school days: 24, 25, 30, 31 Mar, 1, 2 Apr.
        self.assertEqual(
            (easter["effective_start"], easter["effective_end"], easter["days_off"], easter["school_days"]),
            ("2027-03-24", "2027-04-04", 12, 6),
        )

    def test_vocational_schools_have_their_own(self):
        result = server.get_holiday_windows("DE", 2027, "DE-MV", school_group="DE-MV-BBS")
        easter = next(w for w in result["school_windows"] if w["name"] == "Easter Holidays")
        # Mon 22 Mar - Fri 2 Apr, stretched over the weekends either side.
        self.assertEqual((easter["effective_start"], easter["effective_end"]), ("2027-03-20", "2027-04-04"))

    def test_plan_checks_against_the_familys_school_type(self):
        self.assertEqual(server.check_ledger(ROSTOCK)["issues"], [])
        # Vocational schools: winter is 8-13 Feb and summer starts 12 Jul, so
        # neither trip fits inside a school holiday.
        issues = server.check_ledger(self.with_group("DE-MV-BBS"))["issues"]
        self.assertEqual(
            sorted((i["code"], i["trip_ids"]) for i in issues),
            [("outside_school_window", ["summer"]), ("outside_school_window", ["winter"])],
        )

    def test_year_budget(self):
        budget = server.get_year_budget(ROSTOCK, 2027)
        self.assertEqual((budget["planned"], budget["remaining"]), (20, 10))

    def test_school_groups_are_named_in_either_language(self):
        from holiplan import service

        german = service.school_groups("DE", "DE-MV", 2027, "DE")
        self.assertEqual(german["default"], "DE-MV-ABS")
        self.assertEqual(
            german["groups"],
            [
                {"code": "DE-MV-ABS", "name": "Allgemeinbildende Schulen", "default": True},
                {"code": "DE-MV-BBS", "name": "Berufliche Schulen", "default": False},
            ],
        )
        english = service.school_groups("DE", "DE-MV", 2027)
        self.assertEqual([g["name"] for g in english["groups"]], ["General Schools", "Vocational Schools"])


@unittest.skipIf(server is None, "mcp is not installed")
class SchleswigHolsteinTests(Recorded):
    """The islands' holidays are listed beside the mainland's, as exceptions."""

    fixture = "de-sh-2027.json"

    def test_the_mainland_holidays_are_shown(self):
        result = server.get_holiday_windows("DE", 2027, "DE-SH")
        self.assertEqual((result["school_groups"], result["school_group"]), ([], None))
        windows: dict[str, list] = {}
        for w in result["school_windows"]:
            windows.setdefault(w["name"], []).append((w["start"], w["end"]))
        # Not the islands' summer to 7 Aug or their autumn from 4 Oct.
        self.assertEqual(windows["Summer Holidays"], [("2027-07-03", "2027-08-14")])
        self.assertEqual(windows["Autumn Holidays"], [("2027-10-11", "2027-10-23")])

    def test_no_school_types_to_choose_from(self):
        from holiplan import service

        self.assertEqual(service.school_groups("DE", "DE-SH", 2027)["groups"], [])


# A family in the village of Appenzell. Appenzell Innerrhoden lists its sports,
# summer and autumn holidays twice: once for Oberegg, once for the other four
# municipalities (Appenzell, Schlatt-Haslen, Gonten, Schwende-Rüte).
APPENZELL = {
    "schema_version": "1",
    "profile": {
        "country": "CH",
        "region": "CH-AI",
        "home": "Appenzell",
        "leave": [{"year": 2027, "allowance": 25}],
    },
    "trips": [
        # Sports holidays (Fri 5 - Sun 14 Feb), Sat 6 - Sun 14 Feb: Mon 8 - Fri 12 Feb.
        trip("sports", "2027-02-06", "2027-02-14"),
        # Summer (from Sat 3 Jul), Sat 3 - Sun 18 Jul: two full weeks, no holidays.
        trip("summer", "2027-07-03", "2027-07-18"),
        # Autumn (Sat 2 - Sun 17 Oct), its second week: Mon 11 - Fri 15 Oct.
        trip("autumn", "2027-10-09", "2027-10-17"),
    ],
}


def living_in(ledger: dict, home: str | None) -> dict:
    return {**ledger, "profile": {**ledger["profile"], "home": home}}


@unittest.skipIf(server is None, "mcp is not installed")
class AppenzellTests(Recorded):
    """Oberegg has its own sports, summer and autumn holidays."""

    fixture = "ch-ai-2027.json"

    def windows(self, home: str | None) -> dict[str, tuple]:
        result = server.get_holiday_windows("CH", 2027, "CH-AI", home=home)
        return {
            w["name"]: (w["start"], w["end"], w["days_off"])
            for w in result["school_windows"]
            if w["start"] >= "2027-01-01"  # not the Christmas holidays ending in January
        }

    def test_the_village_of_appenzell_gets_its_own_dates(self):
        windows = self.windows("Appenzell")
        # Fri 5 - Sun 14 Feb; Thu 4 and Mon 15 are school days: 10 days.
        self.assertEqual(windows["Sports holidays"], ("2027-02-05", "2027-02-14", 10))
        # Sat 3 Jul - Sun 15 Aug: six weeks and two days, 44 days.
        self.assertEqual(windows["Summer holidays"], ("2027-07-03", "2027-08-15", 44))
        # Sat 2 - Sun 17 Oct: two weeks and two days, 16.
        self.assertEqual(windows["Autumn holidays"], ("2027-10-02", "2027-10-17", 16))
        # The same for every municipality.
        self.assertEqual(windows["Spring holidays"], ("2027-04-10", "2027-04-25", 16))

    def test_oberegg_gets_its_own(self):
        windows = self.windows("Oberegg")
        # Sat 30 Jan - Sun 7 Feb: 9 days.
        self.assertEqual(windows["Sports holidays"], ("2027-01-30", "2027-02-07", 9))
        # Sat 10 Jul - Sun 15 Aug: a week shorter, 37 days.
        self.assertEqual(windows["Summer holidays"], ("2027-07-10", "2027-08-15", 37))
        # Sat 2 - Sun 24 Oct: a week longer, 23 days.
        self.assertEqual(windows["Autumn holidays"], ("2027-10-02", "2027-10-24", 23))

    def test_without_a_home_each_holiday_shows_once_covering_both(self):
        for home in (None, "Herisau"):  # no home, or one outside the canton
            result = server.get_holiday_windows("CH", 2027, "CH-AI", home=home)
            names = [w["name"] for w in result["school_windows"]]
            self.assertEqual(
                names,
                [
                    "Christmas holidays",
                    "Sports holidays",
                    "Spring holidays",
                    "Summer holidays",
                    "Autumn holidays",
                    "Christmas holidays",
                ],
            )
            windows = self.windows(home)
            # Oberegg's start (Sat 30 Jan) to the others' end (Sun 14 Feb): 16 days.
            self.assertEqual(windows["Sports holidays"], ("2027-01-30", "2027-02-14", 16))
            self.assertEqual(windows["Summer holidays"], ("2027-07-03", "2027-08-15", 44))
            self.assertEqual(windows["Autumn holidays"], ("2027-10-02", "2027-10-24", 23))

    def test_plan_checks_against_the_familys_town(self):
        self.assertEqual(server.check_ledger(APPENZELL)["issues"], [])
        # Oberegg's sports holidays end on Sun 7 Feb and its summer starts on
        # Sat 10 Jul, so those trips fall partly in term; autumn fits (to 24 Oct).
        issues = server.check_ledger(living_in(APPENZELL, "Oberegg"))["issues"]
        self.assertEqual(
            sorted((i["code"], i["trip_ids"]) for i in issues),
            [("outside_school_window", ["sports"]), ("outside_school_window", ["summer"])],
        )
        # Without a home, nothing is flagged: each trip fits the merged windows.
        self.assertEqual(server.check_ledger(living_in(APPENZELL, None))["issues"], [])

    def test_oberegg_autumn_third_week_is_school_time_in_appenzell(self):
        late = {**APPENZELL, "trips": [trip("autumn", "2027-10-16", "2027-10-24")]}
        self.assertEqual(server.check_ledger(living_in(late, "Oberegg"))["issues"], [])
        issues = server.check_ledger(late)["issues"]
        self.assertEqual([i["code"] for i in issues], ["outside_school_window"])

    def test_year_budget(self):
        # 5 + 10 + 5 leave days.
        budget = server.get_year_budget(APPENZELL, 2027)
        self.assertEqual((budget["planned"], budget["remaining"]), (20, 5))

    def test_st_mauritius_is_kept_outside_oberegg_only(self):
        # Wed 22 Sep is a holiday in Appenzell but not in Oberegg.
        def cost(home: str) -> float:
            ledger = living_in(APPENZELL, home)
            return server.calculate_leave_cost(
                "2027-09-20", "2027-09-24", "CH", "CH-AI", ledger=ledger
            )["leave_days"]

        self.assertEqual((cost("Appenzell"), cost("Oberegg")), (4, 5))

    def test_summary_uses_the_familys_town(self):
        def autumn(home: str) -> tuple:
            result = server.summarise_plan(living_in(APPENZELL, home), today="2027-01-15")
            window = next(w for w in result["school_windows"] if w["name"] == "Autumn holidays")
            return window["effective_start"], window["effective_end"], window["plans"]

        self.assertEqual(autumn("Appenzell"), ("2027-10-02", "2027-10-17", ["autumn"]))
        self.assertEqual(autumn("Oberegg"), ("2027-10-02", "2027-10-24", ["autumn"]))


@unittest.skipIf(server is None, "mcp is not installed")
class GraubuendenTests(Recorded):
    """Moesa has its own Christmas holidays. The data lists every school
    holiday per district, none for the canton as a whole."""

    fixture = "ch-gr-2027.json"

    def windows(self, home: str | None) -> list[tuple]:
        result = server.get_holiday_windows("CH", 2027, "CH-GR", home=home)
        return [(w["name"], w["start"], w["end"], w["days_off"]) for w in result["school_windows"]]

    def test_chur(self):
        self.assertEqual(
            self.windows("Chur"),
            [
                # Wed 23 Dec - Tue 5 Jan; Tue 22 Dec and Wed 6 Jan are school
                # days (Epiphany isn't a holiday in Graubünden): 14 days.
                ("Christmas holidays", "2026-12-23", "2027-01-05", 14),
                # Sat 9 - Sun 24 Oct: 16 days.
                ("Autumn holidays", "2027-10-09", "2027-10-24", 16),
                ("Christmas holidays", "2027-12-23", "2028-01-05", 14),
            ],
        )

    def test_moesa_has_only_what_the_data_lists_for_it(self):
        # Mesocco: Thu 24 Dec - Wed 6 Jan, 14 days. The data has no 2027 autumn
        # or Christmas holidays for Moesa yet, so none are shown, rather than
        # the other districts' dates.
        self.assertEqual(
            self.windows("Mesocco"), [("Christmas holidays", "2026-12-24", "2027-01-06", 14)]
        )

    def test_without_a_home_one_christmas_covers_both(self):
        windows = self.windows(None)
        # Wed 23 Dec (most districts) to Wed 6 Jan (Moesa): 15 days.
        self.assertEqual(windows[0], ("Christmas holidays", "2026-12-23", "2027-01-06", 15))
        self.assertEqual(len(windows), 3)


if __name__ == "__main__":
    unittest.main()
