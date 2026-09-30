"""Run the real holiplan server over stdio, with OpenHolidays replaced by a recording.

Used by test_protocol.py to exercise the server end to end -- process launch,
JSON-RPC framing, schemas, errors -- without touching the network:

    python tests/fixture_server.py at-wi-2027.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from holiplan import holidays, server  # noqa: E402


def main(fixture: str) -> None:
    data = json.loads((ROOT / "tests" / "fixtures" / fixture).read_text(encoding="utf-8"))

    def public(country, start, end, region=None, language="EN"):
        return [e for e in data["public"] if start <= e["startDate"] <= end]

    def school(country, start, end, region=None, language="EN"):
        return [e for e in data["school"] if e["startDate"] <= end and e["endDate"] >= start]

    holidays.public_holidays = public
    holidays.school_holidays = school
    holidays.subdivisions = lambda country: data["subdivisions"]
    server.main()


if __name__ == "__main__":
    main(sys.argv[1])
