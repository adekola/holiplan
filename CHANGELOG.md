# Changelog

## 0.2.0

For MCP users nothing breaks: tools, arguments and outputs are unchanged, with new fields
added alongside the old ones.

- **Holiday names in your language.** `get_holiday_windows` and `summarise_plan` take an
  optional `language` (`"EN"`, `"DE"`, ...) for public-holiday and school-holiday names.
- **Every warning has a code and values.** Issues gain `params` (for example the days over
  budget, or the years without a leave account), `get_year_budget` returns structured
  `notices` beside its `warnings`, and deadlines carry `what` and `trip_label`. Apps can now
  word them in any language instead of showing the English `message`.
- Budget checks no longer depend on the wording of the English warnings.
- Messages say "4 days" rather than "4.0 days".
- **`holiplan.service`**: every tool's logic as a plain Python function taking and returning
  JSON-shaped data, for front ends other than MCP. It doesn't need the MCP SDK.

## 0.1.0

First release: holiday windows (official and effective dates, closures, local and half-day
public holidays), leave costs, bridge days, year budgets with carryover, ledger checks,
deadlines, a plan summary and calendar export for Germany, Austria, Switzerland and any other
country OpenHolidays covers.
