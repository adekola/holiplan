"""Golden tests.

The fixtures are real Bavarian (DE-BY) public holidays for 2027 and a real
family plan, so a regression here means the arithmetic a family relies on broke.
Run with: python -m unittest discover -s tests   (or pytest)
"""

from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from holiplan.engine.bridges import bridge_days  # noqa: E402
from holiplan.engine.checks import (  # noqa: E402
    deadlines,
    describe_window,
    effective_window,
    validate_ledger,
)
from holiplan.engine.ics import to_ics  # noqa: E402
from holiplan.engine.leave import leave_cost, year_budget  # noqa: E402
from holiplan.engine.models import DayPattern, Ledger  # noqa: E402
from holiplan.engine.summary import summarise  # noqa: E402

# Bavaria, 2027. Easter Sunday is 28 March 2027.
DE_BY_2027 = {
    "2027-01-01",  # Neujahr
    "2027-01-06",  # Heilige Drei Koenige
    "2027-03-26",  # Karfreitag
    "2027-03-29",  # Ostermontag
    "2027-05-01",  # Tag der Arbeit
    "2027-05-06",  # Christi Himmelfahrt
    "2027-05-17",  # Pfingstmontag
    "2027-05-27",  # Fronleichnam
    "2027-10-03",  # Tag der Deutschen Einheit
    "2027-11-01",  # Allerheiligen
    "2027-12-25",
    "2027-12-26",
}

LEDGER = {
    "schema_version": "1",
    "profile": {
        "country": "DE",
        "region": "DE-BY",
        "home": "Regensburg",
        "max_leg_minutes": 240,
        "children": [
            {"alias": "older", "interests": ["swimming", "castles", "drawing"]},
            {"alias": "younger", "interests": ["horses"]},
        ],
        "leave": [
            {
                "year": 2027,
                "allowance": 26,
                "carryover": 5,
                "carryover_expires": "2027-06-30",
            }
        ],
        "visited": ["Salzburg", "Lake Constance"],
        "excluded": ["Paris"],
    },
    "trips": [
        {
            "id": "feb-home",
            "label": "Fasching week at home",
            "start": "2027-02-08",
            "end": "2027-02-12",
            "intent": "home",
            "status": "locked",
        },
        {
            "id": "easter",
            "label": "Lake Garda",
            "start": "2027-03-20",
            "end": "2027-03-29",
            "destination": "Lake Garda",
            "status": "booked",
            "bookings": [
                {
                    "what": "accommodation",
                    "status": "booked",
                    "cancel_by": "2027-03-01",
                }
            ],
        },
        {
            "id": "pentecost",
            "label": "Black Forest",
            "start": "2027-05-16",
            "end": "2027-05-23",
            "destination": "Black Forest",
            "status": "locked",
            "bookings": [{"what": "accommodation", "status": "todo", "book_by": "2027-02-01"}],
        },
        {
            "id": "wedding",
            "label": "Cousin's wedding",
            "start": "2027-05-27",
            "end": "2027-05-30",
            "destination": "Hamburg",
            "intent": "obligation",
            "status": "booked",
            "bookings": [{"what": "accommodation", "status": "booked"}],
        },
    ],
}


class LeaveCostTests(unittest.TestCase):
    def cost(self, start: str, end: str) -> float:
        return leave_cost(date.fromisoformat(start), date.fromisoformat(end), DE_BY_2027).leave_days

    def test_easter_week_costs_four(self):
        # Good Friday and Easter Monday are holidays; 22-25 March are leave.
        self.assertEqual(self.cost("2027-03-20", "2027-03-29"), 4)

    def test_pentecost_week_costs_four(self):
        # Whit Monday is a holiday; 18-21 May are leave.
        self.assertEqual(self.cost("2027-05-16", "2027-05-23"), 4)

    def test_wedding_weekend_costs_one(self):
        # Corpus Christi falls on the Thursday, so only the Friday is leave.
        self.assertEqual(self.cost("2027-05-27", "2027-05-30"), 1)

    def test_fasching_week_would_cost_five(self):
        self.assertEqual(self.cost("2027-02-08", "2027-02-12"), 5)

    def test_holidays_used_are_reported(self):
        cost = leave_cost(date(2027, 3, 20), date(2027, 3, 29), DE_BY_2027)
        self.assertEqual(cost.public_holidays_used, ["2027-03-26", "2027-03-29"])
        self.assertEqual(cost.days_away, 10)
        self.assertEqual(cost.weekend_days, 4)

    def test_half_days_count_as_half(self):
        pattern = DayPattern(half_days=["12-24", "12-31"])
        cost = leave_cost(date(2027, 12, 24), date(2027, 12, 24), set(), pattern)
        self.assertEqual(cost.leave_days, 0.5)

    def test_half_day_public_holiday_costs_half(self):
        # Sechseläuten, Monday 19 April 2027: the afternoon is off in Zürich.
        cost = leave_cost(
            date(2027, 4, 19), date(2027, 4, 23), set(), half_holidays={"2027-04-19"}
        )
        self.assertEqual(cost.leave_days, 4.5)
        self.assertEqual(cost.half_day_holidays_used, ["2027-04-19"])

    def test_half_day_holiday_on_a_personal_half_day_still_costs_half(self):
        pattern = DayPattern(half_days=["04-19"])
        cost = leave_cost(
            date(2027, 4, 19), date(2027, 4, 19), set(), pattern, half_holidays={"2027-04-19"}
        )
        self.assertEqual(cost.leave_days, 0.5)

    def test_half_day_holiday_on_a_weekend_is_just_a_weekend(self):
        cost = leave_cost(date(2027, 4, 17), date(2027, 4, 18), set(), half_holidays={"2027-04-17"})
        self.assertEqual((cost.leave_days, cost.half_day_holidays_used), (0, []))

    def test_four_day_week_ignores_fridays(self):
        pattern = DayPattern(workdays=[0, 1, 2, 3])
        cost = leave_cost(date(2027, 2, 8), date(2027, 2, 12), set(), pattern)
        self.assertEqual(cost.leave_days, 4)


class BudgetTests(unittest.TestCase):
    def setUp(self):
        self.ledger = Ledger.from_dict(LEDGER)

    def test_planned_days_add_up(self):
        budget = year_budget(self.ledger, 2027, DE_BY_2027)
        # Feb week is at home (0), Easter 4, Pentecost 4, wedding 1.
        self.assertEqual(budget.planned, 9)
        self.assertEqual(budget.remaining, 22)

    def test_home_week_is_excluded_from_the_budget(self):
        # The February week would cost 5 days if we took it off, but intent
        # "home" means nobody does -- it occupies the window without leave.
        feb = self.ledger.get("feb-home")
        self.assertFalse(feb.consumes_leave)
        self.assertEqual(leave_cost(feb.start_date, feb.end_date, DE_BY_2027).leave_days, 5)

    def test_home_week_can_opt_into_leave(self):
        data = {**LEDGER, "trips": [{**LEDGER["trips"][0], "takes_leave": True}]}
        budget = year_budget(Ledger.from_dict(data), 2027, DE_BY_2027)
        self.assertEqual(budget.planned, 5)

    def test_carryover_is_used_before_expiry(self):
        budget = year_budget(self.ledger, 2027, DE_BY_2027)
        self.assertEqual(budget.carryover_used_before_expiry, 5)
        self.assertEqual(budget.carryover_at_risk, 0)

    def test_carryover_at_risk_is_flagged(self):
        data = {**LEDGER, "trips": [LEDGER["trips"][3]]}  # only the May obligation
        budget = year_budget(Ledger.from_dict(data), 2027, DE_BY_2027)
        self.assertEqual(budget.carryover_at_risk, 4)
        self.assertTrue(any("expire" in w for w in budget.warnings))

    def test_half_day_holidays_reach_the_budget(self):
        # Easter trip costs 4; a half-day holiday on 23 March takes half a day off it.
        data = {**LEDGER, "trips": [LEDGER["trips"][1]]}
        budget = year_budget(Ledger.from_dict(data), 2027, DE_BY_2027, half_holidays={"2027-03-23"})
        self.assertEqual(budget.planned, 3.5)

    def test_over_budget_is_flagged(self):
        data = {
            **LEDGER,
            "profile": {**LEDGER["profile"], "leave": [{"year": 2027, "allowance": 5, "carryover": 0}]},
        }
        budget = year_budget(Ledger.from_dict(data), 2027, DE_BY_2027)
        self.assertLess(budget.remaining, 0)
        self.assertTrue(any("Over budget" in w for w in budget.warnings))


class LedgerCheckTests(unittest.TestCase):
    def test_clean_ledger_has_no_errors(self):
        issues = validate_ledger(Ledger.from_dict(LEDGER), DE_BY_2027)
        self.assertEqual([i for i in issues if i["level"] == "error"], [])

    def test_overlap_is_caught(self):
        data = {
            **LEDGER,
            "trips": LEDGER["trips"]
            + [
                {
                    "id": "clash",
                    "label": "Clashing trip",
                    "start": "2027-05-20",
                    "end": "2027-05-25",
                }
            ],
        }
        issues = validate_ledger(Ledger.from_dict(data), DE_BY_2027)
        self.assertTrue(any(i["code"] == "overlap" for i in issues))

    def test_trip_outside_school_window_is_flagged(self):
        windows = [{"name": "Pentecost", "start": "2027-05-15", "end": "2027-05-30"}]
        issues = validate_ledger(Ledger.from_dict(LEDGER), DE_BY_2027, windows)
        codes = {i["code"] for i in issues}
        self.assertIn("outside_school_window", codes)

    def test_weekend_before_a_window_is_fine_but_a_school_day_is_not(self):
        # Brief acceptance: Easter runs Mon 22 Mar - Fri 2 Apr 2027 in Bavaria.
        def flagged(start: str) -> bool:
            data = {**LEDGER, "trips": [{"id": "e", "label": "Easter", "start": start, "end": "2027-03-28"}]}
            issues = validate_ledger(Ledger.from_dict(data), DE_BY_2027, DE_BY_2027_WINDOWS)
            return any(i["code"] == "outside_school_window" for i in issues)

        self.assertFalse(flagged("2027-03-20"))  # Saturday
        self.assertTrue(flagged("2027-03-19"))  # Friday, a school day

    def test_home_week_needs_nothing_booked(self):
        items = deadlines(Ledger.from_dict(LEDGER), today=date(2027, 1, 15))
        self.assertNotIn("feb-home", {i["trip_id"] for i in items})
        camp = {**LEDGER["trips"][0], "id": "camp", "intent": "camp"}
        items = deadlines(Ledger.from_dict({**LEDGER, "trips": [camp]}), today=date(2027, 1, 15))
        self.assertEqual([i["kind"] for i in items], ["unbooked"])

    def test_deadlines_are_sorted_and_flag_overdue(self):
        items = deadlines(Ledger.from_dict(LEDGER), today=date(2027, 2, 15))
        self.assertEqual(items, sorted(items, key=lambda d: d["due"]))
        overdue = [i for i in items if i["overdue"]]
        self.assertTrue(any(i["trip_id"] == "pentecost" for i in overdue))


# The raw DE-BY 2027 windows as OpenHolidays returns them: Monday to Friday.
DE_BY_2027_WINDOWS = [
    {"name": "Spring Holidays", "start": "2027-02-08", "end": "2027-02-12"},
    {"name": "Easter Holidays", "start": "2027-03-22", "end": "2027-04-02"},
    {"name": "Pentecost Holidays", "start": "2027-05-18", "end": "2027-05-28"},
]


class EffectiveWindowTests(unittest.TestCase):
    def window(self, start: str, end: str) -> tuple[str, str]:
        s, e = effective_window(date.fromisoformat(start), date.fromisoformat(end), DE_BY_2027)
        return s.isoformat(), e.isoformat()

    def test_easter_takes_in_both_weekends(self):
        # Sat 20 Mar to Sun 4 Apr: the 16 days a family actually has.
        self.assertEqual(self.window("2027-03-22", "2027-04-02"), ("2027-03-20", "2027-04-04"))

    def test_pentecost_takes_in_whit_monday_and_the_weekend_before(self):
        # Whit Monday (17 May) sits just before the window, then the weekend.
        self.assertEqual(self.window("2027-05-18", "2027-05-28"), ("2027-05-15", "2027-05-30"))

    def test_fasching_takes_in_both_weekends(self):
        self.assertEqual(self.window("2027-02-08", "2027-02-12"), ("2027-02-06", "2027-02-14"))

    def test_four_day_week_stretches_further(self):
        # With Fridays off, Fri 19 Mar is free too.
        s, _ = effective_window(
            date(2027, 3, 22), date(2027, 4, 2), DE_BY_2027, DayPattern(workdays=[0, 1, 2, 3])
        )
        self.assertEqual(s, date(2027, 3, 19))

    def test_real_plan_sits_inside_the_windows(self):
        # Easter starts on the Saturday and the Black Forest on the Sunday, both before
        # the official Monday start. Neither should be flagged.
        issues = validate_ledger(Ledger.from_dict(LEDGER), DE_BY_2027, DE_BY_2027_WINDOWS)
        self.assertNotIn("outside_school_window", {i["code"] for i in issues})

    def test_trip_leaving_on_a_school_day_is_still_flagged(self):
        early = {
            "id": "early",
            "label": "Early Easter getaway",
            "start": "2027-03-18",  # Thursday, a school day
            "end": "2027-03-28",
        }
        data = {**LEDGER, "trips": [early]}
        issues = validate_ledger(Ledger.from_dict(data), DE_BY_2027, DE_BY_2027_WINDOWS)
        flagged = [i for i in issues if i["code"] == "outside_school_window"]
        self.assertEqual([i["trip_ids"] for i in flagged], [["early"]])


class IcsTests(unittest.TestCase):
    def test_ics_has_events_and_exclusive_end(self):
        text = to_ics(Ledger.from_dict(LEDGER))
        self.assertIn("BEGIN:VCALENDAR", text)
        self.assertEqual(text.count("BEGIN:VEVENT"), 4)
        # 20-29 March inclusive -> DTEND 30 March
        self.assertIn("DTSTART;VALUE=DATE:20270320", text)
        self.assertIn("DTEND;VALUE=DATE:20270330", text)

    def test_home_week_is_free_time(self):
        text = to_ics(Ledger.from_dict(LEDGER))
        self.assertIn("TRANSP:TRANSPARENT", text)

    def test_long_lines_fold_by_octets(self):
        data = {**LEDGER, "trips": [{**LEDGER["trips"][1], "notes": "Wörthersee über Brücken " * 8}]}
        text = to_ics(Ledger.from_dict(data))
        for line in text.split("\r\n"):
            self.assertLessEqual(len(line.encode("utf-8")), 75)
        unfolded = text.replace("\r\n ", "")
        self.assertIn("DESCRIPTION:" + "Wörthersee über Brücken " * 8, unfolded)

    def test_deadline_uids_are_stable_and_unique(self):
        def uids(ledger: dict) -> list[str]:
            text = to_ics(Ledger.from_dict(ledger))
            return [l for l in text.split("\r\n") if l.startswith("UID:deadline-")]

        before = uids(LEDGER)
        self.assertEqual(len(before), len(set(before)))
        # A new deadline sorting first must not renumber the existing ones.
        earlier = {
            "id": "jan",
            "label": "January ski day",
            "start": "2027-01-16",
            "end": "2027-01-16",
            "bookings": [{"what": "lift pass", "book_by": "2027-01-02"}],
        }
        after = uids({**LEDGER, "trips": LEDGER["trips"] + [earlier]})
        self.assertTrue(set(before) <= set(after))


# Bavaria, 2027 into 2028: Christmas falls on a weekend, 6 January is a Thursday.
NEW_YEAR_HOLIDAYS = DE_BY_2027 | {"2028-01-01", "2028-01-06"}
NEW_YEAR_TRIP = {
    "id": "new-year",
    "label": "Christmas in the mountains",
    "start": "2027-12-27",
    "end": "2028-01-07",
}


class NewYearTests(unittest.TestCase):
    def ledger(self, years: list[int]) -> Ledger:
        leave = [{"year": y, "allowance": 26} for y in years]
        return Ledger.from_dict(
            {**LEDGER, "profile": {**LEDGER["profile"], "leave": leave}, "trips": [NEW_YEAR_TRIP]}
        )

    def test_each_year_pays_only_for_its_own_days(self):
        ledger = self.ledger([2027, 2028])
        # 27-31 Dec: five workdays. 3-7 Jan: five workdays less Epiphany.
        self.assertEqual(year_budget(ledger, 2027, NEW_YEAR_HOLIDAYS).planned, 5)
        self.assertEqual(year_budget(ledger, 2028, NEW_YEAR_HOLIDAYS).planned, 4)

    def test_missing_leave_account_is_flagged(self):
        issues = validate_ledger(self.ledger([2027]), NEW_YEAR_HOLIDAYS)
        missing = [i for i in issues if i["code"] == "no_leave_account"]
        self.assertEqual(len(missing), 1)
        self.assertIn("2028", missing[0]["message"])

    def test_no_warning_when_every_year_has_an_account(self):
        issues = validate_ledger(self.ledger([2027, 2028]), NEW_YEAR_HOLIDAYS)
        self.assertNotIn("no_leave_account", {i["code"] for i in issues})


class DescribeWindowTests(unittest.TestCase):
    def describe(self, start: str, end: str) -> dict:
        return describe_window("w", date.fromisoformat(start), date.fromisoformat(end), DE_BY_2027)

    def test_school_days_skip_weekends_and_public_holidays(self):
        # Easter: 22 Mar - 2 Apr less Good Friday and Easter Monday.
        easter = self.describe("2027-03-22", "2027-04-02")
        self.assertEqual(easter["school_days"], 8)
        self.assertEqual(easter["days_off"], 16)
        self.assertFalse(easter["closure"])

    def test_a_bridge_day_is_a_closure(self):
        # The Friday after Ascension (6 May) only.
        bridge = self.describe("2027-05-06", "2027-05-08")
        self.assertEqual(bridge["school_days"], 1)
        self.assertTrue(bridge["closure"])
        self.assertEqual((bridge["effective_start"], bridge["effective_end"]), ("2027-05-06", "2027-05-09"))

    def test_two_school_days_is_still_a_closure_but_three_is_not(self):
        self.assertTrue(self.describe("2027-10-04", "2027-10-05")["closure"])
        self.assertFalse(self.describe("2027-10-04", "2027-10-06")["closure"])


class BridgeDayTests(unittest.TestCase):
    """Bavaria 2027, worked out by hand from the calendar."""

    def spans(self, **kwargs) -> list[tuple[str, str, float]]:
        options = bridge_days(2027, DE_BY_2027, **kwargs)
        return [(o.start, o.end, o.days_off_per_leave_day) for o in options]

    def test_every_option_best_value_first(self):
        self.assertEqual(
            self.spans(),
            [
                ("2027-05-06", "2027-05-09", 4.0),  # Friday after Ascension
                ("2027-05-27", "2027-05-30", 4.0),  # Friday after Corpus Christi
                ("2027-01-01", "2027-01-06", 3.0),  # 4-5 Jan, up to Epiphany
                ("2027-01-01", "2027-01-10", 2.5),  # 4-5 and 7-8 Jan
                ("2027-03-20", "2027-03-29", 2.5),  # Holy Week, Mon-Thu
                ("2027-03-26", "2027-04-04", 2.5),  # Easter week, Tue-Fri
                ("2027-01-06", "2027-01-10", 2.5),  # 7-8 Jan
                ("2027-05-01", "2027-05-09", 2.25),  # Ascension week
                ("2027-05-15", "2027-05-23", 2.25),  # after Whit Monday
                ("2027-05-22", "2027-05-30", 2.25),  # Corpus Christi week
                ("2027-10-30", "2027-11-07", 2.25),  # after All Saints
                ("2027-05-01", "2027-05-06", 2.0),  # Mon-Wed before Ascension
                ("2027-05-22", "2027-05-27", 2.0),  # Mon-Wed before Corpus Christi
            ],
        )

    def test_option_details(self):
        best = bridge_days(2027, DE_BY_2027)[0]
        self.assertEqual(best.leave_dates, ["2027-05-07"])
        self.assertEqual((best.leave_days, best.days_off), (1, 4))
        self.assertEqual(best.public_holidays, ["2027-05-06"])

    def test_leave_limit(self):
        self.assertEqual(
            [s[:2] for s in self.spans(max_leave=1)],
            [("2027-05-06", "2027-05-09"), ("2027-05-27", "2027-05-30")],
        )

    def test_says_whether_the_kids_are_off_too(self):
        windows = [
            effective_window(date.fromisoformat(w["start"]), date.fromisoformat(w["end"]), DE_BY_2027)
            for w in DE_BY_2027_WINDOWS
        ]
        school = {(o.start, o.end): o.school for o in bridge_days(2027, DE_BY_2027, school_windows=windows)}
        self.assertEqual(school[("2027-05-06", "2027-05-09")], "term")
        self.assertEqual(school[("2027-05-27", "2027-05-30")], "holiday")  # Pentecost runs to 30 May
        self.assertEqual(school[("2027-03-20", "2027-03-29")], "holiday")
        self.assertEqual(school[("2027-05-01", "2027-05-09")], "term")

    def test_half_day_holiday_halves_a_gap_day(self):
        # Pretend 7 May were a half-day holiday: the Ascension bridge costs 0.5.
        best = bridge_days(2027, DE_BY_2027, half_holidays={"2027-05-07"})[0]
        self.assertEqual((best.start, best.leave_days, best.days_off_per_leave_day), ("2027-05-06", 0.5, 8.0))

    def test_part_time_pattern_moves_the_bridges(self):
        # Mon-Thu worker: Fridays are already off.
        options = bridge_days(2027, DE_BY_2027, DayPattern(workdays=[0, 1, 2, 3]))
        spans = {(o.start, o.end): o.leave_days for o in options}
        # Thursday 7 Jan joins Epiphany (Wed) to a Friday-Sunday: 5 days for 1.
        self.assertEqual((options[0].start, options[0].end, options[0].leave_days), ("2027-01-06", "2027-01-10", 1))
        # Ascension (Thu) is a long weekend for free; Mon-Wed before it bridges
        # from Friday 30 Apr to Sunday 9 May.
        self.assertEqual(spans[("2027-04-30", "2027-05-09")], 3)
        self.assertNotIn(("2027-05-06", "2027-05-09"), spans)


class SummaryTests(unittest.TestCase):
    WINDOWS = DE_BY_2027_WINDOWS + [{"name": "Summer", "start": "2027-08-02", "end": "2027-09-13"}]

    def summary(self, today: date, ledger: dict = LEDGER) -> dict:
        windows = [
            describe_window(w["name"], date.fromisoformat(w["start"]), date.fromisoformat(w["end"]), DE_BY_2027)
            for w in self.WINDOWS
        ]
        return summarise(Ledger.from_dict(ledger), DE_BY_2027, windows, today)

    def test_trips_carry_their_leave_cost(self):
        trips = self.summary(date(2027, 1, 15))["trips"]
        self.assertEqual(
            [(t["id"], t["leave_days"]) for t in trips],
            [("feb-home", 0), ("easter", 4), ("pentecost", 4), ("wedding", 1)],
        )
        self.assertEqual(trips[2]["bookings_todo"], 1)

    def test_windows_list_their_plans_and_the_open_ones(self):
        result = self.summary(date(2027, 1, 15))
        plans = {w["name"]: w["plans"] for w in result["school_windows"]}
        self.assertEqual(plans["Pentecost Holidays"], ["pentecost", "wedding"])
        self.assertEqual(
            result["open_windows"],
            [{"name": "Summer", "effective_start": "2027-07-31", "effective_end": "2027-09-13", "days_off": 45}],
        )

    def test_budget_counts_and_issues(self):
        result = self.summary(date(2027, 1, 15))
        self.assertEqual(result["counts"], {"locked": 2, "booked": 2})
        self.assertEqual(result["years"][0]["planned"], 9)
        self.assertEqual(result["issues"], [])

    def test_deadlines_split_into_overdue_and_upcoming(self):
        early = self.summary(date(2027, 1, 15))
        self.assertEqual(early["overdue"], [])
        self.assertEqual([d["due"] for d in early["upcoming"]], ["2027-02-01", "2027-03-01"])
        late = self.summary(date(2027, 3, 15))
        self.assertEqual([d["due"] for d in late["overdue"]], ["2027-02-01", "2027-03-01"])

    def test_children_by_alias_and_age_only(self):
        children = [{"alias": "older", "birth_year": 2019, "interests": ["horses"]}]
        ledger = {**LEDGER, "profile": {**LEDGER["profile"], "children": children}}
        family = self.summary(date(2027, 1, 15), ledger)["family"]
        self.assertEqual(family["children"], [{"alias": "older", "age_this_year": 8, "interests": ["horses"]}])


class EffectiveWindowGuardTests(unittest.TestCase):
    def test_pattern_without_workdays_terminates(self):
        start, end = effective_window(
            date(2027, 3, 22), date(2027, 4, 2), DE_BY_2027, DayPattern(workdays=[])
        )
        self.assertLess(start, date(2027, 3, 22))
        self.assertGreater(end, date(2027, 4, 2))


if __name__ == "__main__":
    unittest.main()
