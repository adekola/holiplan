# Changelog

## 0.5.0

- **School holidays by town.** Where OpenHolidays lists a region's school holidays per
  municipality or district (Appenzell Innerrhoden: Oberegg's sports, summer and autumn
  holidays differ from the rest of the canton; Graubünden: Moesa's Christmas), the profile's
  `home` now picks the ones that apply, the same way it already picks local public holidays.
  This applies to holiday windows, checks, bridge days and summaries; ledgers need no
  migration.
- **Without a home**, or with one the data doesn't know, a holiday whose dates differ by town
  is shown once, from the earliest start to the latest end, instead of twice.
  **Change:** windows and checks for those regions differ from 0.4.0; record the family's
  town in `home` to get its own dates.
- `holiplan.holidays.reaches` says whether a holiday applies to at least part of a
  subdivision.

## 0.4.0

- **One school type's holidays.** Where OpenHolidays lists a region's school holidays per
  school type (Mecklenburg-Vorpommern: general and vocational schools; Bern, Zürich,
  Solothurn), the new optional profile field `school_group` (an OpenHolidays group code such
  as `"DE-MV-ABS"`) picks whose holidays count. Without it the general schools are used. This
  applies to holiday windows, checks, bridge days and summaries; ledgers need no migration.
  **Change:** windows that only applied to other school types (Zürich's Ascension bridge for
  secondary schools) no longer appear by default.
- `get_holiday_windows` takes an optional `school_group` and returns `school_groups` (the
  year's group codes) and `school_group` (the one used).
- `holiplan.service.school_groups` lists a region's school types with names in German or
  English, for an app to offer a choice.
- **Exceptions give way.** Entries OpenHolidays tags as exceptions (Schleswig-Holstein's
  islands, one Gymnasium in Basel-Landschaft) are dropped where a regular holiday of the same
  name overlaps them, so those regions show one summer holiday, not two.

## 0.3.0

- **Calendar to-dos in German.** `export_ics` (and `holiplan.service.calendar`) take an optional
  `language`; booking and cancellation to-dos are worded in German for `"DE"`, English otherwise.
  Trip names stay as the user wrote them.
- To-do UIDs are now built from the deadline itself (kind, trip, what), not its wording, so
  switching language updates a calendar's to-dos instead of duplicating them. Calendars that
  imported an earlier export see each to-do replaced once.

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
