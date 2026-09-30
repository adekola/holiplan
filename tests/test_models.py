"""Reading ledgers: migrations and the errors a malformed ledger gets."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from holiplan.engine import models  # noqa: E402
from holiplan.engine.models import Ledger, Trip, migrate  # noqa: E402
from test_engine import LEDGER  # noqa: E402


def with_trip(**changes) -> dict:
    trip = {"id": "t1", "label": "Lakes", "start": "2027-05-16", "end": "2027-05-23", **changes}
    return {**LEDGER, "trips": [trip]}


class ParseErrorTests(unittest.TestCase):
    def assertRejected(self, data: dict, *fragments: str) -> None:
        with self.assertRaises(ValueError) as caught:
            Ledger.from_dict(data)
        for fragment in fragments:
            self.assertIn(fragment, str(caught.exception))

    def test_unknown_trip_field_is_refused_and_named(self):
        self.assertRejected(with_trip(price=900), "Trip 't1'", "price")

    def test_unknown_profile_field_is_refused_not_dropped(self):
        self.assertRejected({**LEDGER, "profile": {**LEDGER["profile"], "pets": 1}}, "profile", "pets")

    def test_bad_choice_lists_the_allowed_ones(self):
        self.assertRejected(with_trip(intent="holiday"), "intent", "trip, home, camp, obligation")

    def test_bad_date_is_named(self):
        self.assertRejected(with_trip(end="23.05.2027"), "Trip 't1'", "end", "2027-03-20")

    def test_missing_field_is_named(self):
        data = with_trip()
        del data["trips"][0]["end"]
        self.assertRejected(data, "Trip 't1' is missing end")

    def test_nested_errors_say_where(self):
        self.assertRejected(
            with_trip(id="Easter", bookings=[{"what": "hotel", "status": "paid"}]),
            "Booking 1 of trip 'Easter'",
            "status",
        )
        leave = [{"year": 2027, "allowance": 26, "carryover_expires": "June"}]
        self.assertRejected(
            {**LEDGER, "profile": {**LEDGER["profile"], "leave": leave}}, "Leave account 1"
        )

    def test_a_trip_on_its_own_gets_the_same_checks(self):
        with self.assertRaisesRegex(ValueError, "status"):
            Trip.from_dict({"id": "x", "label": "x", "start": "2027-01-01", "end": "2027-01-02", "status": "done"})

    def test_not_an_object(self):
        self.assertRejected({**LEDGER, "trips": ["2027-05-16"]}, "should be a JSON object")


class MigrationTests(unittest.TestCase):
    def test_current_ledger_is_untouched(self):
        self.assertEqual(migrate(LEDGER), LEDGER)

    def test_missing_version_is_version_one(self):
        data = {k: v for k, v in LEDGER.items() if k != "schema_version"}
        self.assertEqual(Ledger.from_dict(data).schema_version, "1")

    def test_newer_ledger_is_refused(self):
        with self.assertRaisesRegex(ValueError, "Update the server"):
            migrate({**LEDGER, "schema_version": "2"})

    def test_migrations_run_in_order(self):
        steps = {
            "1": lambda d: {**d, "trips": [], "note": "from 1"},
            "2": lambda d: {k: v for k, v in d.items() if k != "note"},
        }
        with mock.patch.object(models, "SCHEMA_VERSION", "3"), mock.patch.dict(models.MIGRATIONS, steps):
            upgraded = migrate(LEDGER)
        self.assertEqual(upgraded["schema_version"], "3")
        self.assertEqual(upgraded["trips"], [])
        self.assertNotIn("note", upgraded)


class RoundTripTests(unittest.TestCase):
    def test_ledger_survives_a_round_trip(self):
        once = Ledger.from_dict(LEDGER).to_dict()
        self.assertEqual(Ledger.from_dict(once).to_dict(), once)

    def test_example_ledger_loads(self):
        example = json.loads((ROOT / "examples" / "ledger.example.json").read_text(encoding="utf-8"))
        self.assertEqual(len(Ledger.from_dict(example).trips), len(example["trips"]))


if __name__ == "__main__":
    unittest.main()
