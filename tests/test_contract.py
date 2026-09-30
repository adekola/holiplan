"""The contract around the ledger: models, JSON schema, resources and prompts.

The schema and the dataclasses describe the same ledger twice. These tests
fail when they drift apart, and check that everything the server hands out --
round-tripped ledgers, the starter ledger, the example -- is valid against the
schema it serves.
"""

from __future__ import annotations

import asyncio
import json
import sys
import unittest
from dataclasses import fields
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from holiplan.engine import models  # noqa: E402
from test_engine import LEDGER  # noqa: E402
from test_golden_dach import VIENNA, ZURICH  # noqa: E402

SCHEMA = json.loads((ROOT / "src" / "holiplan" / "schemas" / "ledger.schema.json").read_text("utf-8"))

try:
    import jsonschema
except ImportError:
    jsonschema = None

try:
    from holiplan import server
except ImportError:  # mcp not installed
    server = None


def _names(cls) -> set[str]:
    return {f.name for f in fields(cls)}


class SchemaMatchesModelsTests(unittest.TestCase):
    def test_every_object_has_the_same_fields(self):
        defs = SCHEMA["$defs"]
        profile = defs["profile"]["properties"]
        trip = defs["trip"]["properties"]
        pairs = [
            (models.Ledger, SCHEMA["properties"]),
            (models.FamilyProfile, profile),
            (models.Child, profile["children"]["items"]["properties"]),
            (models.DayPattern, profile["day_pattern"]["properties"]),
            (models.LeaveAccount, profile["leave"]["items"]["properties"]),
            (models.Trip, trip),
            (models.Booking, trip["bookings"]["items"]["properties"]),
        ]
        for cls, properties in pairs:
            with self.subTest(cls.__name__):
                self.assertEqual(_names(cls), set(properties))

    def test_choices_match(self):
        from typing import get_args

        trip = SCHEMA["$defs"]["trip"]["properties"]
        self.assertEqual(list(get_args(models.TripIntent)), trip["intent"]["enum"])
        self.assertEqual(list(get_args(models.TripStatus)), trip["status"]["enum"])
        self.assertEqual(
            list(get_args(models.LocalHolidays)),
            SCHEMA["$defs"]["profile"]["properties"]["local_holidays"]["enum"],
        )

    def test_schema_version_matches(self):
        self.assertEqual(SCHEMA["properties"]["schema_version"]["const"], models.SCHEMA_VERSION)


@unittest.skipIf(jsonschema is None, "jsonschema is not installed")
class ValidAgainstSchemaTests(unittest.TestCase):
    def assertValid(self, ledger: dict) -> None:
        jsonschema.Draft202012Validator(SCHEMA).validate(ledger)

    def test_round_tripped_ledgers_are_valid(self):
        # to_dict writes null for every unset optional field.
        for ledger in (LEDGER, VIENNA, ZURICH):
            self.assertValid(models.Ledger.from_dict(ledger).to_dict())

    def test_example_ledger_is_valid(self):
        self.assertValid(json.loads((ROOT / "examples" / "ledger.example.json").read_text("utf-8")))

    def test_schema_rejects_what_the_models_reject(self):
        data = {**LEDGER, "trips": [{**LEDGER["trips"][0], "price": 900}]}
        with self.assertRaises(jsonschema.ValidationError):
            self.assertValid(data)

    @unittest.skipIf(server is None, "mcp is not installed")
    def test_starter_ledger_is_valid_and_loads(self):
        starter = server.starter_ledger(date(2027, 9, 1))
        self.assertValid(starter)
        ledger = models.Ledger.from_dict(starter)
        self.assertEqual([a.year for a in ledger.profile.leave], [2027, 2028])


@unittest.skipIf(server is None, "mcp is not installed")
class ServedTests(unittest.TestCase):
    def run_async(self, coroutine):
        return asyncio.run(coroutine)

    def test_resources(self):
        uris = {str(r.uri) for r in self.run_async(server.mcp.list_resources())}
        self.assertEqual(
            uris,
            {
                "holiplan://schema/ledger",
                "holiplan://schema/profile",
                "holiplan://schema/trip",
                "holiplan://ledger/starter",
            },
        )

    def test_part_schemas_stand_alone(self):
        profile = json.loads(server.profile_schema())
        self.assertEqual(profile["$id"], "urn:holiplan:profile:1")
        self.assertIn("country", profile["properties"])
        trip = json.loads(server.trip_schema())
        self.assertIn("bookings", trip["properties"])

    def test_the_brief_prompts_are_all_there(self):
        prompts = {p.name: p.title for p in self.run_async(server.mcp.list_prompts())}
        self.assertEqual(
            prompts,
            {
                "set_up_profile": "Set up my family profile",
                "plan_my_year": "Plan my year",
                "plan_window": "Plan this holiday window",
                "whats_next": "What needs doing next?",
                "partner_brief": "Make a brief for my partner",
            },
        )

    def test_every_prompt_asks_for_the_ledger_round_trip(self):
        for text in (
            server.set_up_profile(),
            server.plan_my_year(2028),
            server.plan_window("Summer"),
            server.whats_next(),
            server.partner_brief(),
        ):
            self.assertIn("give me the whole updated ledger", text)

    def test_plan_my_year_defaults_to_next_year(self):
        self.assertIn(f"for {date.today().year + 1}.", server.plan_my_year())

    def test_tools(self):
        names = {t.name for t in self.run_async(server.mcp.list_tools())}
        self.assertEqual(
            names,
            {
                "get_holiday_windows",
                "calculate_leave_cost",
                "find_bridge_days",
                "get_year_budget",
                "upsert_trip",
                "check_ledger",
                "list_deadlines",
                "summarise_plan",
                "export_ics",
            },
        )


if __name__ == "__main__":
    unittest.main()
