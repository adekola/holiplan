# holiplan

An MCP server for planning a family's year around school holidays and a limited leave allowance.

<!-- mcp-name: io.github.adekola/holiplan -->

Assistants are good at suggesting destinations and bad at remembering that Whit Monday is a
public holiday, that five carried-over days expire in June, and that you already said no
drive over three and a half hours. This server does the parts that should be calculated;
the assistant does the parts that need judgement.

Holiday data comes from [OpenHolidays](https://openholidaysapi.org) (CC BY 4.0), which covers
public and school holidays for a growing list of countries, with no API key.

## Status

Pre-release (0.1). Stateless: your ledger is a JSON file you keep, and the server stores nothing.
See [PRIVACY.md](https://github.com/adekola/holiplan/blob/main/PRIVACY.md) for exactly what goes where, and
[docs/examples.md](https://github.com/adekola/holiplan/blob/main/docs/examples.md) for what a conversation looks like.

## Use it in Claude Desktop

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then add holiplan to
`claude_desktop_config.json` and restart Claude:

```json
{
  "mcpServers": {
    "holiplan": {
      "command": "uvx",
      "args": ["holiplan"]
    }
  }
}
```

`uvx` fetches holiplan from PyPI and runs it; there is nothing else to install. Without uv,
`pip install holiplan` and use `"command": "holiplan"` with no `args`.

Start with the **Set up my family profile** prompt, which builds a ledger with you one
question at a time, or copy [`examples/ledger.example.json`](https://github.com/adekola/holiplan/blob/main/examples/ledger.example.json) to `my-ledger.json` and edit it.
Tell Claude where the file is, and save the updated ledger whenever Claude hands it back.
A conversation then looks like:

> Here's my ledger. What do the 2027 school holidays look like, and what can I afford?

> Add two weeks on the Danish coast from 17 July.

> What needs booking next?

[docs/examples.md](https://github.com/adekola/holiplan/blob/main/docs/examples.md) has these conversations in full, with real figures.

## Tools

| Tool | Purpose |
| --- | --- |
| `get_holiday_windows` | School holidays (official and effective dates), single-day closures, and public holidays marked local, half-day and observed |
| `calculate_leave_cost` | Leave days a given trip needs, with the holidays inside it listed |
| `find_bridge_days` | The leave days that buy the most time off, and whether the kids are off too |
| `get_year_budget` | Planned versus available leave, including carryover expiry |
| `upsert_trip` | Add or update a trip, then re-check the ledger |
| `check_ledger` | Overlaps, budget breaches, bad dates, trips outside school holidays |
| `list_deadlines` | Booking deadlines and cancel-by dates, soonest first |
| `summarise_plan` | The whole plan at a glance, for a brief to your partner |
| `export_ics` | The plan as a calendar file |

Resources: JSON schemas for the ledger, profile and trip (`holiplan://schema/...`), and a blank
starter ledger (`holiplan://ledger/starter`).

Prompts: *Set up my family profile*, *Plan my year*, *Plan this holiday window*,
*What needs doing next?*, *Make a brief for my partner*.

## Design notes

- **The ledger is the source of truth.** Tools take it in and hand it back, so the plan
  doesn't drift between conversations.
- **Intent decides leave.** A week with intent `home` or `camp` occupies a school holiday
  window without costing leave. Override with `takes_leave`.
- **Carryover is spent first**, but only by trips ending before the expiry date, so
  "these days expire in June" shows up as a warning while you can still act on it.
- **A trip over New Year** costs each year only its own days.
- **Local holidays follow your home town.** Some holidays cover only part of a region, like
  Augsburg's Peace Festival in Bavaria. With the profile's `home` set to a town, only that
  town's local holidays count as days off; without a home they all do. Set `local_holidays`
  to `"include"` or `"exclude"` to override. `ignore_holidays` (`"MM-DD"`) covers what the
  data can't tell apart, such as Assumption Day (`"08-15"`), which is only a holiday in
  Catholic-majority Bavarian towns.
- **School windows include the free days around them.** The official Monday-to-Friday dates
  are stretched over adjacent weekends and public holidays before trips are checked against them.
  School holidays of two school days or fewer, like All Souls' Day in Austria, are listed
  separately as closures.
- **Half-day holidays cost half a day.** Zürich's Sechseläuten afternoon is a half day off,
  not a full one.
- **Regions accept ISO codes.** OpenHolidays uses its own codes where they differ (Vienna is
  `AT-WI`, not `AT-9`); either works, and an unknown region is an error rather than a quietly
  incomplete year.
- **Ledgers are versioned.** Older ledgers are migrated when read, a newer one is refused, and
  a malformed one gets an error naming the trip and field at fault.
- **When OpenHolidays is down**, the server serves the last copy it fetched, or says plainly
  that it can't check dates right now. It never guesses.
- **No real names.** Children are identified by alias and interests only.
- **The server never books anything.** It knows what needs booking and by when; you book it
  and record the result.

## Roadmap

- [ ] Drive-time filtering (OpenRouteService or self-hosted OSRM)
- [ ] Remote deployment over Streamable HTTP
- [ ] Publish to the MCP registry
- [ ] School holiday windows by school type, where the data supports it (Zürich splits
  primary, secondary and vocational; windows already list the types they apply to)

## Development

```bash
git clone https://github.com/adekola/holiplan.git
cd holiplan
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
python -m unittest discover -s tests                # offline
```

To run your working copy in Claude Desktop, point `command` at the venv's Python
(`.venv/bin/python`, or `.venv\Scripts\python.exe` on Windows) with
`"args": ["-m", "holiplan.server"]`.

## Licence

MIT, see [LICENSE](https://github.com/adekola/holiplan/blob/main/LICENSE). Holiday data: [OpenHolidays](https://openholidaysapi.org), CC BY 4.0.
