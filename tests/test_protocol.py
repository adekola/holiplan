"""End to end over MCP: the server as a subprocess, spoken to over stdio.

Everything else calls tool functions directly. These tests go through what a
real assistant does -- process launch, handshake, JSON-RPC, tool schemas,
structured results and error reporting -- with OpenHolidays replaced by the
recorded Vienna data (see fixture_server.py), so they stay offline.
"""

from __future__ import annotations

import json
import sys
import unittest
from contextlib import asynccontextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from holiplan.engine.models import Ledger  # noqa: E402

try:
    from mcp import Client
    from mcp.client.stdio import StdioServerParameters

    from test_golden_dach import VIENNA  # noqa: E402
except ImportError:  # mcp not installed
    Client = None

ENV = {"PYTHONPATH": str(ROOT / "src"), "PYTHONIOENCODING": "utf-8"}


@unittest.skipIf(Client is None, "mcp is not installed")
class OverStdio(unittest.IsolatedAsyncioTestCase):
    args = [str(ROOT / "tests" / "fixture_server.py"), "at-wi-2027.json"]

    # anyio needs the connection opened and closed in the same task, which rules
    # out asyncSetUp/asyncTearDown: each test body runs inside `connected()`.
    @asynccontextmanager
    async def connected(self):
        params = StdioServerParameters(command=sys.executable, args=self.args, env=ENV, cwd=ROOT)
        async with Client(params) as client:
            self.client = client
            yield client

    async def call(self, tool: str, **arguments):
        result = await self.client.call_tool(tool, arguments)
        self.assertFalse(result.is_error, result.content[0].text if result.content else result)
        return result.structured_content

    async def error(self, tool: str, **arguments) -> str:
        result = await self.client.call_tool(tool, arguments)
        self.assertTrue(result.is_error)
        return result.content[0].text


class HandshakeTests(OverStdio):
    async def test_server_describes_itself(self):
        async with self.connected():
            self.assertIn("pass it to every tool", self.client.instructions)
            tools = (await self.client.list_tools()).tools
            self.assertEqual(len(tools), 9)
            for tool in tools:
                with self.subTest(tool.name):
                    self.assertTrue(tool.description)
                    self.assertEqual(tool.input_schema["type"], "object")
            self.assertEqual(len((await self.client.list_prompts()).prompts), 5)
            self.assertEqual(len((await self.client.list_resources()).resources), 4)


class ScenarioTests(OverStdio):
    """A Vienna family's plan, one tool call at a time, as an assistant would."""

    async def test_plan_a_trip_and_check_the_year(self):
        async with self.connected():
            cost = await self.call(
                "calculate_leave_cost", start="2027-03-20", end="2027-03-29", country="AT", region="AT-9", ledger=VIENNA
            )
            self.assertEqual(cost["leave_days"], 5)

            # Add a Christmas trip: 27-31 Dec 2027 is five workdays.
            christmas = {"id": "xmas", "label": "Christmas", "start": "2027-12-24", "end": "2028-01-02"}
            upserted = await self.call("upsert_trip", ledger=VIENNA, trip=christmas)
            ledger = upserted["ledger"]
            self.assertIn("xmas", [t["id"] for t in ledger["trips"]])
            # 2028 has no leave account, and 2027 is now over budget (see below).
            self.assertEqual([i["code"] for i in upserted["issues"]], ["no_leave_account", "over_budget"])

            budget = await self.call("get_year_budget", ledger=ledger, year=2027)
            # 23 planned before, plus Fri 24 Dec and Mon 27 - Fri 31 Dec: 29 of 25.
            self.assertEqual(budget["planned"], 29)
            self.assertIn("Over budget", budget["warnings"][0])

            # The ledger the server handed back is one it can read again.
            Ledger.from_dict(ledger)

    async def test_bridges_summary_deadlines_and_calendar(self):
        async with self.connected():
            bridges = await self.call("find_bridge_days", country="AT", year=2027, region="AT-9", ledger=VIENNA, limit=1)
            self.assertEqual(bridges["options"][0]["leave_dates"], ["2027-05-07"])

            summary = await self.call("summarise_plan", ledger=VIENNA, today="2027-01-15")
            self.assertEqual([w["name"] for w in summary["open_windows"]], ["Christmas Holidays"])

            deadlines = await self.call("list_deadlines", ledger=VIENNA, today="2027-01-15")
            # All four trips are ideas with nothing booked yet.
            self.assertEqual([d["trip_id"] for d in deadlines["result"]], ["semester", "easter", "summer", "autumn"])
            self.assertEqual({d["kind"] for d in deadlines["result"]}, {"unbooked"})

            ics = await self.call("export_ics", ledger=VIENNA)
            self.assertTrue(ics["result"].startswith("BEGIN:VCALENDAR\r\n"))
            self.assertEqual(ics["result"].count("BEGIN:VEVENT"), 4)

    async def test_holiday_windows(self):
        async with self.connected():
            windows = await self.call("get_holiday_windows", country="AT", year=2027, region="AT-9", home="Wien")
            self.assertEqual(windows["region"], "AT-WI")
            self.assertEqual(len(windows["school_closures"]), 3)


class ErrorTests(OverStdio):
    """Deliberate errors must reach the assistant word for word."""

    async def test_unknown_region(self):
        async with self.connected():
            text = await self.error("get_holiday_windows", country="AT", year=2027, region="AT-10")
            self.assertIn("Use one of", text)
            self.assertIn("AT-WI (Wien)", text)

    async def test_malformed_ledger(self):
        async with self.connected():
            broken = {**VIENNA, "trips": [{"id": "x", "label": "x", "start": "2027-01-01"}]}
            self.assertIn("Trip 'x' is missing end", await self.error("check_ledger", ledger=broken))

    async def test_bad_date(self):
        async with self.connected():
            text = await self.error("calculate_leave_cost", start="20.03.2027", end="2027-03-29", country="AT")
            self.assertIn("20.03.2027", text)


class ResourceAndPromptTests(OverStdio):
    async def test_starter_ledger_loads(self):
        async with self.connected():
            result = await self.client.read_resource("holiplan://ledger/starter")
            self.assertEqual(result.contents[0].mime_type, "application/json")
            Ledger.from_dict(json.loads(result.contents[0].text))

    async def test_part_schema(self):
        async with self.connected():
            result = await self.client.read_resource("holiplan://schema/trip")
            self.assertEqual(json.loads(result.contents[0].text)["$id"], "urn:holiplan:trip:1")

    async def test_prompt_with_argument(self):
        async with self.connected():
            prompt = await self.client.get_prompt("plan_my_year", {"year": "2028"})
            self.assertIn("for 2028.", prompt.messages[0].content.text)


class EntryPointTests(OverStdio):
    """The real `python -m holiplan.server`, unpatched. Listing needs no network."""

    args = ["-m", "holiplan.server"]

    async def test_starts_and_lists_tools(self):
        async with self.connected():
            names = {t.name for t in (await self.client.list_tools()).tools}
            self.assertIn("find_bridge_days", names)


if __name__ == "__main__":
    unittest.main()
